"""In-memory ElevenLabs.

Enforces the two properties the link and verify steps depend on: an agent name
is unique (so adoption works), and a phone number's assignment is stored rather
than assumed. Verify reads back what assign wrote, so a fake that silently
succeeded would make the verify step meaningless.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.errors import VendorError
from app.providers.fakes.support import Behaviour, instance_token
from app.providers.models import (
    AgentRef,
    Delivery,
    PhoneNumberRef,
    TransferTarget,
    VoiceRef,
    WebhookTool,
)

#: A stand-in catalogue, shaped like the real one.
#:
#: The ids are the three this product ships configured, so a test that picks
#: "the first voice" picks something the rest of the system recognizes. The
#: fourth is deliberately *not* in the configured map: it is what proves the
#: picker offers more than the three styles, and that choosing one of those
#: extras survives a publish.
#:
#: Sample URLs are obviously fake. Nothing plays them in a test, and a real
#: vendor URL hard-coded here would be a network request inside the suite.
FAKE_VOICES: tuple[VoiceRef, ...] = (
    VoiceRef(
        voice_id="21m00Tcm4TlvDq8ikWAM",
        name="Janet - Clear, Professional",
        description="Young adult woman with a clear, professional tone.",
        accent="american",
        gender="female",
        age="young",
        use_case="conversational",
        language="en",
        preview_url="https://example.invalid/voices/janet.mp3",
        category="professional",
    ),
    VoiceRef(
        voice_id="EXAVITQu4vr4xnSDxMaL",
        name="Sarah - Mature, Reassuring, Confident",
        description="Young adult woman with a confident and warm, mature quality.",
        accent="american",
        gender="female",
        age="young",
        use_case="conversational",
        language="en",
        preview_url="https://example.invalid/voices/sarah.mp3",
        category="premade",
    ),
    VoiceRef(
        voice_id="onwK4e9ZLuTAKqWW03F9",
        name="Daniel - Steady Broadcaster",
        description="Middle aged man with a formal, steady delivery.",
        accent="british",
        gender="male",
        age="middle_aged",
        use_case="informative_educational",
        language="en",
        preview_url="https://example.invalid/voices/daniel.mp3",
        category="premade",
    ),
    VoiceRef(
        voice_id="cjVigY5qzO86Huf0OWal",
        name="Eric - Smooth, Trustworthy",
        description="Middle aged man with a smooth, trustworthy tone.",
        accent="american",
        gender="male",
        age="middle_aged",
        use_case="conversational",
        language="en",
        preview_url="https://example.invalid/voices/eric.mp3",
        category="premade",
    ),
    # Not English. Present so the filter that keeps the picker to voices the
    # call path can actually speak has something to exclude.
    VoiceRef(
        voice_id="g3YpdjT1OTh9cunaumJs",
        name="Layla - Modern Arabic",
        description="Middle aged woman with a confident tone.",
        accent="jordanian",
        gender="female",
        age="middle_aged",
        use_case="narrative_story",
        language="ar",
        preview_url="https://example.invalid/voices/layla.mp3",
        category="professional",
    ),
)


@dataclass
class _StoredAgent:
    agent_id: str
    name: str
    voice_id: str
    system_prompt: str
    first_message: str
    #: The complete set the last push carried. A push with none empties it,
    #: which is what lets a test prove that deleting the last transfer rule
    #: stops the vendor forwarding rather than quietly leaving it in place.
    transfers: tuple[TransferTarget, ...] = ()
    #: How the last push said to speak. ``None`` is the vendor's default, and a
    #: test can tell that apart from an explicit 1.0.
    delivery: Delivery | None = None
    #: The workspace tools the last push attached. Replaced, never merged.
    tool_ids: tuple[str, ...] = ()

    def to_ref(self) -> AgentRef:
        return AgentRef(
            agent_id=self.agent_id,
            name=self.name,
            voice_id=self.voice_id,
            system_prompt=self.system_prompt,
            first_message=self.first_message,
        )


@dataclass
class _StoredPhone:
    phone_id: str
    e164: str
    label: str
    assigned_agent_id: str | None = None

    def to_ref(self) -> PhoneNumberRef:
        return PhoneNumberRef(
            phone_id=self.phone_id,
            e164=self.e164,
            assigned_agent_id=self.assigned_agent_id,
            label=self.label,
        )


@dataclass
class FakeElevenLabsProvider:
    name: str = "fake-elevenlabs"
    behaviour: Behaviour = field(default_factory=Behaviour)
    agents: dict[str, _StoredAgent] = field(default_factory=dict)
    phones: dict[str, _StoredPhone] = field(default_factory=dict)
    #: Workspace tools by id, exactly as they were last written.
    tools: dict[str, WebhookTool] = field(default_factory=dict)
    # Same reasoning as the Twilio fake: both of these land in UNIQUE
    # columns, so they must survive a worker restart without colliding.
    _token: str = field(default_factory=lambda: instance_token(8))
    _agent_counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))
    _phone_counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))
    _tool_counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    # ---- voices ----------------------------------------------------------
    async def list_voices(self) -> Sequence[VoiceRef]:
        self.behaviour.record("list_voices")
        return FAKE_VOICES

    # ---- agents ----------------------------------------------------------
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
        self.behaviour.record("create_agent", name=name, voice_id=voice_id)

        if any(agent.name == name for agent in self.agents.values()):
            raise VendorError(
                "an agent with that name already exists",
                vendor=self.name,
                status_code=409,
                retryable=False,
                code="agent_exists",
                details={"name": name},
            )

        agent = _StoredAgent(
            agent_id=f"agent_{self._token}{next(self._agent_counter):08d}",
            name=name,
            voice_id=voice_id,
            system_prompt=system_prompt,
            first_message=first_message,
            transfers=tuple(transfers),
            delivery=delivery,
            tool_ids=self._known_tools(tool_ids),
        )
        self.agents[agent.agent_id] = agent
        return agent.to_ref()

    async def get_agent(self, *, agent_id: str) -> AgentRef | None:
        self.behaviour.record("get_agent", agent_id=agent_id)
        agent = self.agents.get(agent_id)
        return agent.to_ref() if agent else None

    async def find_agent_by_name(self, *, name: str) -> AgentRef | None:
        self.behaviour.record("find_agent_by_name", name=name)
        for agent in self.agents.values():
            if agent.name == name:
                return agent.to_ref()
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
        self.behaviour.record("update_agent", agent_id=agent_id, transfers=len(transfers))
        agent = self.agents.get(agent_id)
        if agent is None:
            raise VendorError(
                "agent not found",
                vendor=self.name,
                status_code=404,
                retryable=False,
                code="agent_not_found",
                details={"agent_id": agent_id},
            )
        agent.name = name
        agent.system_prompt = system_prompt
        agent.first_message = first_message
        agent.voice_id = voice_id
        # Replaced, never merged: the caller passes the complete set.
        agent.transfers = tuple(transfers)
        agent.delivery = delivery
        agent.tool_ids = self._known_tools(tool_ids)
        return agent.to_ref()

    async def delete_agent(self, *, agent_id: str) -> None:
        self.behaviour.record("delete_agent", agent_id=agent_id)
        # Idempotent: compensation may run more than once.
        self.agents.pop(agent_id, None)
        for phone in self.phones.values():
            if phone.assigned_agent_id == agent_id:
                phone.assigned_agent_id = None

    # ---- workspace tools -------------------------------------------------
    def _known_tools(self, tool_ids: Sequence[str]) -> tuple[str, ...]:
        """The vendor refuses an agent that references a tool it does not have."""
        missing = [tool_id for tool_id in tool_ids if tool_id not in self.tools]
        if missing:
            raise VendorError(
                "unknown tool id",
                vendor=self.name,
                status_code=422,
                retryable=False,
                code="tool_not_found",
                details={"tool_ids": missing},
            )
        return tuple(tool_ids)

    async def upsert_webhook_tool(self, *, tool_id: str | None, tool: WebhookTool) -> str:
        self.behaviour.record("upsert_webhook_tool", tool_id=tool_id, name=tool.name)
        if tool_id and tool_id in self.tools:
            self.tools[tool_id] = tool
            return tool_id
        created = f"tool_{self._token}{next(self._tool_counter):08d}"
        self.tools[created] = tool
        return created

    async def delete_tool(self, *, tool_id: str) -> None:
        self.behaviour.record("delete_tool", tool_id=tool_id)
        self.tools.pop(tool_id, None)

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
        self.behaviour.record("import_phone_number", e164=e164, twilio_sid=twilio_sid)

        if not twilio_account_sid or not twilio_auth_token:
            # ElevenLabs validates the Twilio credentials at import time.
            raise VendorError(
                "twilio credentials are required to import a number",
                vendor=self.name,
                status_code=422,
                retryable=False,
                code="missing_twilio_credentials",
            )

        for phone in self.phones.values():
            if phone.e164 == e164:
                # Re-importing is not an error at the vendor; it returns the
                # existing record. The link step depends on that being safe.
                return phone.to_ref()

        phone = _StoredPhone(
            phone_id=f"phnum_{self._token}{next(self._phone_counter):08d}", e164=e164, label=label
        )
        self.phones[phone.phone_id] = phone
        return phone.to_ref()

    async def find_phone_number(self, *, e164: str) -> PhoneNumberRef | None:
        self.behaviour.record("find_phone_number", e164=e164)
        for phone in self.phones.values():
            if phone.e164 == e164:
                return phone.to_ref()
        return None

    async def assign_agent_to_number(self, *, phone_id: str, agent_id: str) -> PhoneNumberRef:
        self.behaviour.record("assign_agent_to_number", phone_id=phone_id, agent_id=agent_id)

        phone = self.phones.get(phone_id)
        if phone is None:
            raise VendorError(
                "phone number not found",
                vendor=self.name,
                status_code=404,
                retryable=False,
                code="phone_not_found",
                details={"phone_id": phone_id},
            )
        if agent_id not in self.agents:
            raise VendorError(
                "agent not found",
                vendor=self.name,
                status_code=404,
                retryable=False,
                code="agent_not_found",
                details={"agent_id": agent_id},
            )
        phone.assigned_agent_id = agent_id
        return phone.to_ref()

    async def delete_phone_number(self, *, phone_id: str) -> None:
        self.behaviour.record("delete_phone_number", phone_id=phone_id)
        self.phones.pop(phone_id, None)
