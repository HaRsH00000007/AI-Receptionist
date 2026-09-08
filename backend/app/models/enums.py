"""Domain enumerations and the provisioning state machine.

The step-to-status mapping at the bottom of this module is the spine of the
whole system (docs/01_PLAN_POC.md, "The provisioning state machine"). It lives
here, next to the enums, so that the database, the worker and the admin panel
cannot hold three different opinions about what follows what.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def enum_column(enum_class: type[StrEnum], name: str) -> SAEnum:
    """A VARCHAR column constrained to an enum's values by a CHECK constraint.

    ``native_enum=False`` keeps the values out of a Postgres ``TYPE``, so adding
    a provisioning step later is an ordinary column alteration rather than an
    ``ALTER TYPE``. ``validate_strings`` makes SQLAlchemy reject a bad value
    before it reaches the database.
    """
    return SAEnum(
        enum_class,
        name=name,
        native_enum=False,
        length=48,
        validate_strings=True,
        values_callable=lambda enum: [member.value for member in enum],
    )


# ---------------------------------------------------------------------------
# Tenant
# ---------------------------------------------------------------------------
class BusinessType(StrEnum):
    """Drives which prompt template a tenant's config is generated from.

    Deliberately a closed set: docs/00_DECISIONS.md section 3 constrains the
    LLM's business-type routing to "the enum of templates we have".
    """

    SALON = "salon"
    LEGAL = "legal"
    MEDICAL = "medical"
    REAL_ESTATE = "real_estate"
    OTHER = "other"


class TenantPlan(StrEnum):
    """``TRIAL`` is the internal default; the signup form offers the other three."""

    TRIAL = "trial"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class GreetingStyle(StrEnum):
    """How the receptionist opens a call.

    Also the deterministic key into the voice map: the style the owner picked on
    the form selects the ElevenLabs voice, never an LLM
    (docs/00_DECISIONS.md section 3).
    """

    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    FORMAL = "formal"


class TenantStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    FAILED = "failed"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------
class ProvisioningStep(StrEnum):
    """One step of the state machine. Each becomes a Temporal activity later."""

    VALIDATE = "validate"
    GENERATE_CONFIG = "generate_config"
    PURCHASE_NUMBER = "purchase_number"
    CREATE_AGENT = "create_agent"
    LINK_NUMBER = "link_number"
    VERIFY = "verify"
    ACTIVATE = "activate"


class ProvisioningStatus(StrEnum):
    """Where a run currently is.

    The first eight values are the happy path, in order; the last three are
    off-path. ``COMPENSATING`` means side effects are being unwound (releasing a
    number, deleting an agent); ``COMPENSATED`` is where an abandoned run ends.
    """

    DRAFT = "draft"
    VALIDATED = "validated"
    CONFIG_GENERATED = "config_generated"
    NUMBER_PURCHASED = "number_purchased"
    AGENT_CREATED = "agent_created"
    NUMBER_LINKED = "number_linked"
    VERIFIED = "verified"
    ACTIVE = "active"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------
class PhoneNumberStatus(StrEnum):
    """``PENDING`` exists so a row can be written *before* Twilio is called.

    That ordering is what makes a purchase recoverable: if the worker dies
    between the API call and the commit, the pending row and its idempotency key
    are already durable, so the retry adopts the number instead of buying a
    second one.
    """

    PENDING = "pending"
    ACTIVE = "active"
    RELEASED = "released"
    FAILED = "failed"


class AgentStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    FAILED = "failed"
    DELETED = "deleted"


class AgentConfigSource(StrEnum):
    """How a config was produced.

    ``TEMPLATE_FALLBACK`` records that the LLM failed schema validation and the
    deterministic renderer produced the config instead, so signup never
    hard-blocks on the LLM (docs/01_PLAN_POC.md, "LLM usage in the POC").
    """

    LLM = "llm"
    TEMPLATE_FALLBACK = "template_fallback"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
class WebhookProvider(StrEnum):
    ELEVENLABS = "elevenlabs"
    TWILIO = "twilio"


class WebhookStatus(StrEnum):
    """``DUPLICATE`` is a success, not a failure: providers retry deliveries by
    design, and a second delivery must be a visible no-op rather than a second
    call record."""

    RECEIVED = "received"
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------
class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallStatus(StrEnum):
    RECEIVED = "received"
    TRANSCRIBED = "transcribed"
    SUMMARIZED = "summarized"
    NOTIFIED = "notified"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------
#: Executed in this order. A run's next step is the first one not yet succeeded.
STEP_SEQUENCE: tuple[ProvisioningStep, ...] = (
    ProvisioningStep.VALIDATE,
    ProvisioningStep.GENERATE_CONFIG,
    ProvisioningStep.PURCHASE_NUMBER,
    ProvisioningStep.CREATE_AGENT,
    ProvisioningStep.LINK_NUMBER,
    ProvisioningStep.VERIFY,
    ProvisioningStep.ACTIVATE,
)

#: The status a run reaches once a step succeeds.
STATUS_AFTER_STEP: dict[ProvisioningStep, ProvisioningStatus] = {
    ProvisioningStep.VALIDATE: ProvisioningStatus.VALIDATED,
    ProvisioningStep.GENERATE_CONFIG: ProvisioningStatus.CONFIG_GENERATED,
    ProvisioningStep.PURCHASE_NUMBER: ProvisioningStatus.NUMBER_PURCHASED,
    ProvisioningStep.CREATE_AGENT: ProvisioningStatus.AGENT_CREATED,
    ProvisioningStep.LINK_NUMBER: ProvisioningStatus.NUMBER_LINKED,
    ProvisioningStep.VERIFY: ProvisioningStatus.VERIFIED,
    ProvisioningStep.ACTIVATE: ProvisioningStatus.ACTIVE,
}

#: A run in one of these statuses will never be picked up again.
TERMINAL_RUN_STATUSES: frozenset[ProvisioningStatus] = frozenset(
    {
        ProvisioningStatus.ACTIVE,
        ProvisioningStatus.FAILED,
        ProvisioningStatus.COMPENSATED,
    }
)

#: A run in one of these statuses still owns its tenant. At most one such run may
#: exist per tenant, enforced by a partial unique index rather than by the
#: worker, so that two workers cannot both decide to buy a number
#: (docs/00_DECISIONS.md section 5, "belt-and-braces with a DB constraint").
IN_FLIGHT_RUN_STATUSES: frozenset[ProvisioningStatus] = (
    frozenset(ProvisioningStatus) - TERMINAL_RUN_STATUSES
)


def next_step(completed: ProvisioningStep | None) -> ProvisioningStep | None:
    """The step that follows ``completed``, or ``None`` at the end of the run."""
    if completed is None:
        return STEP_SEQUENCE[0]
    index = STEP_SEQUENCE.index(completed)
    return STEP_SEQUENCE[index + 1] if index + 1 < len(STEP_SEQUENCE) else None


def status_after(step: ProvisioningStep) -> ProvisioningStatus:
    """The status a run moves to when ``step`` succeeds."""
    return STATUS_AFTER_STEP[step]
