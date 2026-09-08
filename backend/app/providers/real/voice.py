"""ElevenLabs Conversational AI, over its REST API.

**Not tested against the live API.** Endpoint paths and payload shapes follow
ElevenLabs' documented Conversational AI interface (``/v1/convai/*``) as of
authoring; the fake provider is what CI exercises, and the response parsing is
written defensively so a renamed field degrades to ``None`` rather than a crash.
Point ``ELEVENLABS_PROVIDER=elevenlabs`` at a real key to try it — see README.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.errors import VendorError
from app.providers.http import ProviderHTTPClient
from app.providers.models import AgentRef, PhoneNumberRef


def _agent_ref(payload: dict[str, Any]) -> AgentRef:
    """Read an agent out of a nested response without assuming every key."""
    conversation = payload.get("conversation_config", {}) or {}
    agent = conversation.get("agent", {}) or {}
    prompt = agent.get("prompt", {}) or {}
    tts = conversation.get("tts", {}) or {}
    if not payload.get("agent_id"):
        raise VendorError(
            "elevenlabs returned an agent without an id",
            vendor="elevenlabs",
            retryable=False,
            code="vendor_bad_response",
            details={"keys": sorted(payload)[:20]},
        )
    return AgentRef(
        agent_id=str(payload["agent_id"]),
        name=payload.get("name", ""),
        voice_id=tts.get("voice_id"),
        first_message=agent.get("first_message"),
        system_prompt=prompt.get("prompt"),
    )


def _phone_ref(payload: dict[str, Any]) -> PhoneNumberRef:
    """Read a phone number out of a response.

    The id is required — everything downstream addresses the number by it — so
    a response without one is reported as a bad vendor response rather than
    raising a bare ``KeyError`` that the engine would misread as an unexpected
    crash and retry.
    """
    assigned = payload.get("assigned_agent") or {}
    phone_id = payload.get("phone_number_id") or payload.get("phone_id")
    if not phone_id:
        raise VendorError(
            "elevenlabs returned a phone number without an id",
            vendor="elevenlabs",
            retryable=False,
            code="vendor_bad_response",
            details={"keys": sorted(payload)[:20]},
        )
    return PhoneNumberRef(
        phone_id=str(phone_id),
        e164=payload.get("phone_number", ""),
        assigned_agent_id=assigned.get("agent_id") or payload.get("agent_id"),
        label=payload.get("label"),
    )


class ElevenLabsRestProvider:
    """Agent lifecycle and Twilio number import/assignment."""

    name = "elevenlabs"

    def __init__(self, settings: Settings) -> None:
        self._llm_model = settings.llm_summary_model
        self._client = ProviderHTTPClient(
            vendor="elevenlabs",
            base_url=settings.elevenlabs_api_base_url,
            timeout_s=settings.provider_timeout_s,
            headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- agents ----------------------------------------------------------
    def _agent_body(
        self, *, name: str, system_prompt: str, first_message: str, voice_id: str
    ) -> dict[str, Any]:
        return {
            "name": name,
            "conversation_config": {
                "agent": {
                    "prompt": {"prompt": system_prompt},
                    "first_message": first_message,
                    "language": "en",
                },
                "tts": {"voice_id": voice_id},
            },
        }

    async def create_agent(
        self, *, name: str, system_prompt: str, first_message: str, voice_id: str
    ) -> AgentRef:
        payload = await self._client.post(
            "/v1/convai/agents/create",
            json=self._agent_body(
                name=name,
                system_prompt=system_prompt,
                first_message=first_message,
                voice_id=voice_id,
            ),
        )
        if not payload or "agent_id" not in payload:
            raise VendorError(
                "elevenlabs create returned no agent_id",
                vendor=self.name,
                retryable=False,
                code="vendor_bad_response",
            )
        # The create response is thin; read the agent back so callers always get
        # the same shape whether the agent was created or adopted.
        created = await self.get_agent(agent_id=payload["agent_id"])
        return created or AgentRef(agent_id=payload["agent_id"], name=name, voice_id=voice_id)

    async def get_agent(self, *, agent_id: str) -> AgentRef | None:
        try:
            payload = await self._client.get(f"/v1/convai/agents/{agent_id}")
        except VendorError as exc:
            if exc.status_code == 404:
                return None
            raise
        return _agent_ref(payload)

    async def find_agent_by_name(self, *, name: str) -> AgentRef | None:
        payload = await self._client.get("/v1/convai/agents", params={"page_size": 100})
        for entry in (payload or {}).get("agents", []):
            if entry.get("name") == name:
                return await self.get_agent(agent_id=entry["agent_id"])
        return None

    async def update_agent(
        self,
        *,
        agent_id: str,
        name: str,
        system_prompt: str,
        first_message: str,
        voice_id: str,
    ) -> AgentRef:
        await self._client.patch(
            f"/v1/convai/agents/{agent_id}",
            json=self._agent_body(
                name=name,
                system_prompt=system_prompt,
                first_message=first_message,
                voice_id=voice_id,
            ),
        )
        updated = await self.get_agent(agent_id=agent_id)
        if updated is None:
            raise VendorError(
                "agent disappeared during update",
                vendor=self.name,
                retryable=True,
                code="agent_missing_after_update",
                details={"agent_id": agent_id},
            )
        return updated

    async def delete_agent(self, *, agent_id: str) -> None:
        try:
            await self._client.delete(f"/v1/convai/agents/{agent_id}", expected=(200, 204))
        except VendorError as exc:
            if exc.status_code == 404:
                return
            raise

    # ---- phone numbers ---------------------------------------------------
    async def import_phone_number(
        self,
        *,
        e164: str,
        twilio_sid: str,
        twilio_account_sid: str,
        twilio_auth_token: str,
        label: str,
    ) -> PhoneNumberRef:
        # Importing the same number twice returns a conflict rather than a
        # duplicate, so an interrupted link step recovers by looking it up.
        try:
            payload = await self._client.post(
                "/v1/convai/phone-numbers",
                json={
                    "phone_number": e164,
                    "label": label,
                    "sid": twilio_account_sid,
                    "token": twilio_auth_token,
                    "provider": "twilio",
                },
            )
        except VendorError as exc:
            if exc.status_code in (400, 409):
                existing = await self.find_phone_number(e164=e164)
                if existing is not None:
                    return existing
            raise
        return _phone_ref(payload)

    async def find_phone_number(self, *, e164: str) -> PhoneNumberRef | None:
        payload = await self._client.get("/v1/convai/phone-numbers")
        entries = payload if isinstance(payload, list) else (payload or {}).get("phone_numbers", [])
        for entry in entries:
            if entry.get("phone_number") == e164:
                return _phone_ref(entry)
        return None

    async def assign_agent_to_number(self, *, phone_id: str, agent_id: str) -> PhoneNumberRef:
        payload = await self._client.patch(
            f"/v1/convai/phone-numbers/{phone_id}", json={"agent_id": agent_id}
        )
        return _phone_ref(payload)

    async def delete_phone_number(self, *, phone_id: str) -> None:
        try:
            await self._client.delete(f"/v1/convai/phone-numbers/{phone_id}", expected=(200, 204))
        except VendorError as exc:
            if exc.status_code == 404:
                return
            raise
