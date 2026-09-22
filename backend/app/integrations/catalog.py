"""The integration catalog: what can be connected, and by whom.

**Vertical-aware by construction.** Each entry names the business types it is
offered to, and :func:`integrations_for` is the only way the rest of the code
reads the catalog — so a law firm is offered Clio and a salon is offered Acuity,
and neither is shown the other's. Adding a vertical-specific integration is one
entry here; nothing in the API or the UI is written around a particular
provider.

**Honest about what works.** Three separate facts decide what a customer sees:

* ``capabilities`` — what the *provider's* API supports. Never more than that.
* ``implemented`` — whether *this codebase* has a working adapter. An entry
  without one is shown as "coming soon", never as connectable.
* whether the operator has configured it (client credentials and the token
  encryption key) — decided at request time by the service, so a deployment
  without Google credentials says "configuration required" instead of offering
  a button that fails.

Nothing here can produce a "connected" state. Only a completed OAuth exchange
writes that (see :mod:`app.integrations.service`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.enums import BusinessType, IntegrationProvider


class IntegrationCategory(StrEnum):
    CALENDAR = "calendar"
    SCHEDULING = "scheduling"
    PRACTICE_MANAGEMENT = "practice_management"
    AUTOMATION = "automation"


class AuthType(StrEnum):
    OAUTH2 = "oauth2"
    #: A URL the customer pastes in (e.g. a Zapier catch hook).
    WEBHOOK = "webhook"


class Capability(StrEnum):
    """What a provider's API can do. Listed only where the API really does it."""

    READ_AVAILABILITY = "read_availability"
    READ_CALENDAR = "read_calendar"
    CREATE_EVENT = "create_event"
    UPDATE_EVENT = "update_event"
    CANCEL_EVENT = "cancel_event"
    BOOK_APPOINTMENT = "book_appointment"
    RESCHEDULE_APPOINTMENT = "reschedule_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"
    FORWARD_CALL_EVENTS = "forward_call_events"


ALL_BUSINESS_TYPES: frozenset[BusinessType] = frozenset(BusinessType)


@dataclass(frozen=True, slots=True)
class IntegrationDefinition:
    id: IntegrationProvider
    name: str
    #: The company behind it, for the card's byline.
    vendor: str
    category: IntegrationCategory
    description: str
    #: A key the frontend maps to its own icon. Never a vendor's logo asset.
    icon: str
    auth_type: AuthType
    capabilities: tuple[Capability, ...]
    supported_business_types: frozenset[BusinessType]
    #: Whether this codebase has a working adapter for it.
    implemented: bool
    #: Where an operator creates the OAuth client, for the "configuration
    #: required" state. Shown to operators, not a customer instruction.
    setup_hint: str = ""


CATALOG: tuple[IntegrationDefinition, ...] = (
    IntegrationDefinition(
        id=IntegrationProvider.GOOGLE_CALENDAR,
        name="Google Calendar",
        vendor="Google",
        category=IntegrationCategory.CALENDAR,
        description=(
            "Let your receptionist check when you're free and book appointments straight "
            "into your Google Calendar, without double-booking."
        ),
        icon="calendar-google",
        auth_type=AuthType.OAUTH2,
        # Calendar API v3: freeBusy.query, events.insert / patch / delete.
        capabilities=(
            Capability.READ_AVAILABILITY,
            Capability.CREATE_EVENT,
            Capability.UPDATE_EVENT,
            Capability.CANCEL_EVENT,
        ),
        supported_business_types=ALL_BUSINESS_TYPES,
        implemented=True,
        setup_hint="GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET",
    ),
    IntegrationDefinition(
        id=IntegrationProvider.MICROSOFT_OUTLOOK,
        name="Microsoft Outlook",
        vendor="Microsoft",
        category=IntegrationCategory.CALENDAR,
        description=(
            "Connect your Outlook or Microsoft 365 calendar so your receptionist can see "
            "your availability and add appointments to it."
        ),
        icon="calendar-outlook",
        auth_type=AuthType.OAUTH2,
        # Microsoft Graph: calendarView, and events create / update / delete.
        capabilities=(
            Capability.READ_AVAILABILITY,
            Capability.CREATE_EVENT,
            Capability.UPDATE_EVENT,
            Capability.CANCEL_EVENT,
        ),
        supported_business_types=ALL_BUSINESS_TYPES,
        implemented=True,
        setup_hint="MICROSOFT_OAUTH_CLIENT_ID / MICROSOFT_OAUTH_CLIENT_SECRET",
    ),
    IntegrationDefinition(
        id=IntegrationProvider.CLIO,
        name="Clio",
        vendor="Clio",
        category=IntegrationCategory.PRACTICE_MANAGEMENT,
        description=(
            "Sync consultations with your Clio Manage calendar, so new client calls land "
            "where your firm already works."
        ),
        icon="scale",
        auth_type=AuthType.OAUTH2,
        # Clio Manage API v4: calendar entries can be listed, created, updated and
        # deleted. It has no free/busy endpoint, so availability is *read from the
        # calendar*, not queried — the capability says exactly that.
        capabilities=(
            Capability.READ_CALENDAR,
            Capability.CREATE_EVENT,
            Capability.UPDATE_EVENT,
            Capability.CANCEL_EVENT,
        ),
        supported_business_types=frozenset({BusinessType.LEGAL}),
        implemented=False,
    ),
    IntegrationDefinition(
        id=IntegrationProvider.ACUITY_SCHEDULING,
        name="Acuity Scheduling",
        vendor="Squarespace",
        category=IntegrationCategory.SCHEDULING,
        description=(
            "Offer callers your real Acuity openings and book them into the right "
            "appointment type while they're on the line."
        ),
        icon="clock",
        auth_type=AuthType.OAUTH2,
        # Acuity API: /availability/times, and appointments create / reschedule /
        # cancel.
        capabilities=(
            Capability.READ_AVAILABILITY,
            Capability.BOOK_APPOINTMENT,
            Capability.RESCHEDULE_APPOINTMENT,
            Capability.CANCEL_APPOINTMENT,
        ),
        supported_business_types=frozenset(
            {BusinessType.SALON, BusinessType.MEDICAL, BusinessType.OTHER}
        ),
        implemented=False,
    ),
    IntegrationDefinition(
        id=IntegrationProvider.ZAPIER,
        name="Zapier",
        vendor="Zapier",
        category=IntegrationCategory.AUTOMATION,
        description=(
            "Send each call's summary, caller and callback number to thousands of apps "
            "through a Zap."
        ),
        icon="zap",
        auth_type=AuthType.WEBHOOK,
        capabilities=(Capability.FORWARD_CALL_EVENTS,),
        # Not offered to medical practices: forwarding call details to a
        # general-purpose automation service is forwarding patient information
        # somewhere no BAA covers.
        supported_business_types=frozenset(
            {
                BusinessType.LEGAL,
                BusinessType.REAL_ESTATE,
                BusinessType.SALON,
                BusinessType.OTHER,
            }
        ),
        implemented=False,
    ),
)

_BY_ID: dict[IntegrationProvider, IntegrationDefinition] = {entry.id: entry for entry in CATALOG}

#: Display order within a vertical: the integration that vertical actually runs
#: on first, then calendars, then automation.
_CATEGORY_ORDER = {
    IntegrationCategory.PRACTICE_MANAGEMENT: 0,
    IntegrationCategory.SCHEDULING: 1,
    IntegrationCategory.CALENDAR: 2,
    IntegrationCategory.AUTOMATION: 3,
}


def integrations_for(business_type: BusinessType) -> list[IntegrationDefinition]:
    """The integrations offered to one business type, in display order."""
    offered = [entry for entry in CATALOG if business_type in entry.supported_business_types]
    return sorted(offered, key=lambda entry: _CATEGORY_ORDER[entry.category])


def definition(provider: IntegrationProvider) -> IntegrationDefinition:
    return _BY_ID[provider]


def offered_to(provider: IntegrationProvider, business_type: BusinessType) -> bool:
    return business_type in _BY_ID[provider].supported_business_types
