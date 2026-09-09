"""In-memory ElevenLabs.

Enforces the two properties the link and verify steps depend on: an agent name
is unique (so adoption works), and a phone number's assignment is stored rather
than assumed. Verify reads back what assign wrote, so a fake that silently
succeeded would make the verify step meaningless.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from app.core.errors import VendorError
from app.providers.fakes.support import Behaviour, instance_token
from app.providers.models import AgentRef, PhoneNumberRef


@dataclass
class _StoredAgent:
    agent_id: str
    name: str
    voice_id: str
    system_prompt: str
    first_message: str

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
    # Same reasoning as the Twilio fake: both of these land in UNIQUE
    # columns, so they must survive a worker restart without colliding.
    _token: str = field(default_factory=lambda: instance_token(8))
    _agent_counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))
    _phone_counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    # ---- agents ----------------------------------------------------------
    async def create_agent(
        self, *, name: str, system_prompt: str, first_message: str, voice_id: str
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
    ) -> AgentRef:
        self.behaviour.record("update_agent", agent_id=agent_id)
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
        return agent.to_ref()

    async def delete_agent(self, *, agent_id: str) -> None:
        self.behaviour.record("delete_agent", agent_id=agent_id)
        # Idempotent: compensation may run more than once.
        self.agents.pop(agent_id, None)
        for phone in self.phones.values():
            if phone.assigned_agent_id == agent_id:
                phone.assigned_agent_id = None

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
