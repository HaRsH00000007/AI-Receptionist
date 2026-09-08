"""Provider interfaces.

Business logic depends on these, never on a vendor SDK. Two consequences that
matter more than the tidiness: the whole provisioning path runs in CI with no
network, and DRY_RUN is a constructor choice rather than a branch scattered
through the steps.

Every method must raise :class:`app.core.errors.VendorError` (or another
:class:`~app.core.errors.AppError`) on failure, with ``retryable`` set — the
orchestrator reads that flag and never inspects vendor exception types.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.providers.models import (
    AgentRef,
    AvailableNumber,
    EmailMessage,
    EmailResult,
    LLMResponse,
    PhoneNumberRef,
    PurchasedNumber,
)


@runtime_checkable
class LLMProvider(Protocol):
    """Language tasks only.

    Never asked to choose an area code, decide a retry, or move a tenant
    between states (docs/00_DECISIONS.md section 3).
    """

    name: str

    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse: ...


@runtime_checkable
class TwilioProvider(Protocol):
    name: str

    async def search_available_numbers(
        self,
        *,
        area_code: str | None = None,
        in_region: str | None = None,
        toll_free: bool = False,
        limit: int = 5,
    ) -> list[AvailableNumber]: ...

    async def purchase_number(self, *, e164: str, friendly_name: str) -> PurchasedNumber: ...

    async def get_number(self, *, sid: str) -> PurchasedNumber | None: ...

    async def find_by_friendly_name(self, *, friendly_name: str) -> PurchasedNumber | None:
        """The adoption guard.

        Called before every purchase. If a previous attempt bought a number and
        died before committing, this finds it and the retry adopts it instead of
        spending again.
        """
        ...

    async def release_number(self, *, sid: str) -> None:
        """Compensation. An unreleased number bills monthly, forever."""
        ...


@runtime_checkable
class ElevenLabsProvider(Protocol):
    name: str

    async def create_agent(
        self,
        *,
        name: str,
        system_prompt: str,
        first_message: str,
        voice_id: str,
    ) -> AgentRef: ...

    async def get_agent(self, *, agent_id: str) -> AgentRef | None: ...

    async def find_agent_by_name(self, *, name: str) -> AgentRef | None:
        """Adoption guard, for the same reason as the Twilio one."""
        ...

    async def update_agent(
        self,
        *,
        agent_id: str,
        name: str,
        system_prompt: str,
        first_message: str,
        voice_id: str,
    ) -> AgentRef:
        """Push our config onto an existing agent. Backs ``resync_agent``."""
        ...

    async def delete_agent(self, *, agent_id: str) -> None: ...

    async def import_phone_number(
        self,
        *,
        e164: str,
        twilio_sid: str,
        twilio_account_sid: str,
        twilio_auth_token: str,
        label: str,
    ) -> PhoneNumberRef: ...

    async def find_phone_number(self, *, e164: str) -> PhoneNumberRef | None: ...

    async def assign_agent_to_number(self, *, phone_id: str, agent_id: str) -> PhoneNumberRef: ...

    async def delete_phone_number(self, *, phone_id: str) -> None: ...


@runtime_checkable
class EmailProvider(Protocol):
    name: str

    async def send(self, message: EmailMessage) -> EmailResult: ...
