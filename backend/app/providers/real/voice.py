"""ElevenLabs Conversational AI, over its REST API.

**Not tested against the live API.** Endpoint paths and payload shapes follow
ElevenLabs' documented Conversational AI interface (``/v1/convai/*``) as of
authoring; the fake provider is what CI exercises, and the response parsing is
written defensively so a renamed field degrades to ``None`` rather than a crash.
Point ``ELEVENLABS_PROVIDER=elevenlabs`` at a real key to try it — see README.

Four exceptions to that.

``tts.speed`` and ``turn.turn_eagerness`` **have** been checked against the live
API, on a throwaway agent that was created, written to, read back and deleted.
The vendor accepted ``speed`` between 0.7 and 1.2 and refused 0.6 and 2.5 by
name; it accepted ``turn_eagerness`` of ``eager`` and refused ``low``/``high``
with "Input should be 'patient', 'normal' or 'eager'". Both ranges in this file
are the vendor's own, not a guess.

``list_voices`` **has** been checked against the live
API: ``GET /v1/voices`` returned the account's catalogue with the name, the
``labels`` (accent, gender, age, language, descriptive, use case), the
``description`` sentence and a public ``preview_url`` per voice, which is
exactly what is parsed below.

And ``_built_in_tools`` **has** been checked against the
live API, on a throwaway agent. The vendor accepted the payload as written and
stored both transfer types (``warm`` as ``conference``, ``cold`` as ``blind``),
a later push with no transfers cleared ``transfer_to_number`` to ``null``, and
``end_call`` survived both — which is what sending the object whole is for.

The workspace tools (``upsert_webhook_tool``, ``delete_tool`` and ``tool_ids``
on the agent) **have** been checked against the live API the same way, on a
throwaway tool and agent: the vendor stored the URL, method, header, required
list and the ``system__`` dynamic variables exactly as sent, patched the tool in
place, attached and detached it by id, and kept ``end_call`` throughout.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.config import Settings
from app.core.errors import VendorError
from app.providers.http import ProviderHTTPClient
from app.providers.models import (
    AgentRef,
    Delivery,
    PhoneNumberRef,
    TransferTarget,
    VoiceRef,
    WebhookTool,
)


def _text(value: Any) -> str | None:
    """A non-empty string, or nothing. Keeps "" out of optional fields."""
    text = str(value).strip() if value is not None else ""
    return text or None


def _label(value: Any) -> str | None:
    """A vendor label, lower-cased, with any locale prefix dropped.

    ``accent`` comes back as "american" on some voices and "en-american" on
    others. The part before the dash is the language, which is read from its
    own label, so keeping it here would print "En-american" on a card.
    """
    text = _text(value)
    if text is None:
        return None
    return text.rsplit("-", 1)[-1].lower()


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
    def _built_in_tools(self, transfers: Sequence[TransferTarget]) -> dict[str, Any]:
        """The vendor's system tools, as a whole object.

        Sent whole on every update, including ``end_call``, because the vendor
        documents ``built_in_tools`` as replacing what the agent has rather than
        merging into it — a payload naming only the transfer tool would take
        the agent's ability to hang up away with it.

        ``transfer_to_number`` is ``None`` when there is nothing to transfer to.
        That is the removal, and it is why the key is always present: a business
        that deletes its last rule must stop forwarding, and leaving the key out
        would leave the vendor's last copy of the rules in force.
        """
        tool: dict[str, Any] | None = None
        if transfers:
            tool = {
                "type": "system",
                "name": "transfer_to_number",
                "params": {
                    "system_tool_type": "transfer_to_number",
                    "transfers": [
                        {
                            "transfer_destination": {
                                "type": "phone",
                                "phone_number": target.e164,
                            },
                            "condition": target.condition,
                            # Warm briefs the person before joining the caller;
                            # cold sends the call straight through. Both need
                            # the number to have been imported from Twilio
                            # natively, which is how this product imports it.
                            "transfer_type": ("conference" if target.kind == "warm" else "blind"),
                        }
                        for target in transfers
                    ],
                },
            }
        return {
            "end_call": {
                "type": "system",
                "name": "end_call",
                "params": {"system_tool_type": "end_call"},
            },
            "transfer_to_number": tool,
        }

    def _agent_body(
        self,
        *,
        name: str,
        system_prompt: str,
        first_message: str,
        voice_id: str,
        transfers: Sequence[TransferTarget] = (),
        delivery: Delivery | None = None,
        tool_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        tts: dict[str, Any] = {"voice_id": voice_id}
        conversation: dict[str, Any] = {
            "agent": {
                "prompt": {
                    "prompt": system_prompt,
                    "built_in_tools": self._built_in_tools(transfers),
                    # The complete set, like the built-in tools: an empty list
                    # is how disconnecting a calendar takes booking away.
                    "tool_ids": list(tool_ids),
                },
                "first_message": first_message,
                "language": "en",
            },
            "tts": tts,
        }
        # Omitted rather than sent as a default. The vendor's own defaults are
        # what every agent had before these were configurable, and writing
        # "speed: 1.0" would make a reset indistinguishable from a choice in the
        # vendor's audit of the agent.
        if delivery is not None and delivery.speed is not None:
            tts["speed"] = delivery.speed
        if delivery is not None and delivery.eagerness is not None:
            conversation["turn"] = {"turn_eagerness": delivery.eagerness}
        return {"name": name, "conversation_config": conversation}

    async def create_agent(
        self,
        *,
        name: str,
        system_prompt: str,
        first_message: str,
        voice_id: str,
        transfers: Sequence[TransferTarget] = (),
        delivery: Delivery | None = None,
        tool_ids: Sequence[str] = (),
    ) -> AgentRef:
        payload = await self._client.post(
            "/v1/convai/agents/create",
            json=self._agent_body(
                name=name,
                system_prompt=system_prompt,
                first_message=first_message,
                voice_id=voice_id,
                transfers=transfers,
                delivery=delivery,
                tool_ids=tool_ids,
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

    async def list_voices(self) -> Sequence[VoiceRef]:
        """The account's voice catalogue.

        One unpaginated read; the account holds tens of voices, not thousands.
        Parsed field by field so a renamed label costs that one detail rather
        than the whole picker — a voice with no ``description`` is still a
        voice somebody can choose and listen to.
        """
        payload = await self._client.get("/v1/voices")
        voices: list[VoiceRef] = []
        for entry in (payload or {}).get("voices", []):
            voice_id = entry.get("voice_id")
            if not voice_id:
                continue
            labels = entry.get("labels") or {}
            voices.append(
                VoiceRef(
                    voice_id=str(voice_id),
                    name=str(entry.get("name") or voice_id),
                    description=_text(entry.get("description")),
                    # The accent label is sometimes bare ("american") and
                    # sometimes locale-prefixed ("en-american"); the prefix is
                    # the language, which is carried separately.
                    accent=_label(labels.get("accent")),
                    gender=_label(labels.get("gender")),
                    age=_label(labels.get("age")),
                    use_case=_label(labels.get("use_case")),
                    language=_label(labels.get("language")),
                    preview_url=_text(entry.get("preview_url")),
                    category=_text(entry.get("category")),
                )
            )
        return voices

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
        transfers: Sequence[TransferTarget] = (),
        delivery: Delivery | None = None,
        tool_ids: Sequence[str] = (),
    ) -> AgentRef:
        await self._client.patch(
            f"/v1/convai/agents/{agent_id}",
            json=self._agent_body(
                name=name,
                system_prompt=system_prompt,
                first_message=first_message,
                voice_id=voice_id,
                transfers=transfers,
                delivery=delivery,
                tool_ids=tool_ids,
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

    # ---- workspace tools -------------------------------------------------
    @staticmethod
    def _tool_config(tool: WebhookTool) -> dict[str, Any]:
        """A webhook tool as the vendor stores it.

        Each body property carries exactly one of a description (the model fills
        it in) or a dynamic variable (the vendor does), which is the vendor's
        own rule for a property.
        """
        properties: dict[str, Any] = {}
        for parameter in tool.parameters:
            entry: dict[str, Any] = {"type": parameter.type}
            if parameter.dynamic_variable:
                entry["dynamic_variable"] = parameter.dynamic_variable
            else:
                entry["description"] = parameter.description
            properties[parameter.name] = entry
        return {
            "type": "webhook",
            "name": tool.name,
            "description": tool.description,
            "response_timeout_secs": tool.timeout_s,
            "api_schema": {
                "url": tool.url,
                "method": "POST",
                "request_headers": dict(tool.headers),
                "request_body_schema": {
                    "type": "object",
                    "description": tool.description,
                    "properties": properties,
                    "required": [
                        parameter.name
                        for parameter in tool.parameters
                        if parameter.required and not parameter.dynamic_variable
                    ],
                },
            },
        }

    async def upsert_webhook_tool(self, *, tool_id: str | None, tool: WebhookTool) -> str:
        body = {"tool_config": self._tool_config(tool)}
        if tool_id:
            try:
                await self._client.patch(f"/v1/convai/tools/{tool_id}", json=body)
                return tool_id
            except VendorError as exc:
                if exc.status_code != 404:
                    raise
                # Deleted at the vendor by hand; make it again below.
        payload = await self._client.post("/v1/convai/tools", json=body)
        created = (payload or {}).get("id") or (payload or {}).get("tool_id")
        if not created:
            raise VendorError(
                "elevenlabs created a tool without an id",
                vendor=self.name,
                retryable=False,
                code="vendor_bad_response",
                details={"keys": sorted(payload or {})[:20]},
            )
        return str(created)

    async def delete_tool(self, *, tool_id: str) -> None:
        try:
            await self._client.delete(f"/v1/convai/tools/{tool_id}", expected=(200, 204))
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
