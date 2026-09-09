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
    #: The money gate. Sits immediately before the only step that spends
    #: real money, and is the reason an unpaid signup cannot buy a number.
    BILLING_GATE = "billing_gate"
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
    BILLING_AUTHORIZED = "billing_authorized"
    NUMBER_PURCHASED = "number_purchased"
    AGENT_CREATED = "agent_created"
    NUMBER_LINKED = "number_linked"
    VERIFIED = "verified"
    ACTIVE = "active"
    FAILED = "failed"
    #: Parked, not failed. The tenant is not entitled to provisioning yet.
    #: Distinct from FAILED on purpose: nothing is broken, no compensation
    #: is owed, and the run resumes the moment billing says yes.
    BILLING_BLOCKED = "billing_blocked"
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


class AgentMode(StrEnum):
    """Which ElevenLabs topology serves a tenant.

    ``PER_TENANT`` is the POC shape: one vendor agent per customer. It works,
    but a prompt improvement means N vendor calls and N chances for one tenant
    to end up on a different prompt than everyone else.

    ``SHARED_VERTICAL`` is the production shape: roughly five agents, one per
    business type, with per-call dynamic variables carrying the business's name,
    hours and services. A prompt improvement then ships once, to everyone, and a
    config rollback becomes a flag move with no vendor call at all.

    Both exist at once so tenants migrate in verified batches.
    """

    PER_TENANT = "per_tenant"
    SHARED_VERTICAL = "shared_vertical"


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
    STRIPE = "stripe"


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
    # Immediately before PURCHASE_NUMBER, which is the only step that
    # spends money. The ordering here is a money-safety property, not a
    # stylistic one.
    ProvisioningStep.BILLING_GATE,
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
    ProvisioningStep.BILLING_GATE: ProvisioningStatus.BILLING_AUTHORIZED,
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


# ---------------------------------------------------------------------------
# Identity and access
# ---------------------------------------------------------------------------


class UserStatus(StrEnum):
    ACTIVE = "active"
    #: Retained for its audit trail rather than deleted. A disabled user keeps
    #: their audit_logs rows meaningful; a deleted one leaves orphan references.
    DISABLED = "disabled"


class MembershipRole(StrEnum):
    """What a user may do inside one organization.

    Ordered by privilege, which :func:`role_at_least` relies on. Roles are per
    membership, not per user: the same person can own one business and be a
    member of another.
    """

    #: Billing and deletion. Exactly one per tenant, enforced by a partial index.
    OWNER = "owner"
    #: Everything except billing and deleting the organization.
    ADMIN = "admin"
    #: Read, plus the day-to-day call handling. No configuration changes.
    MEMBER = "member"


#: Privilege ordering. A separate mapping rather than the enum's declaration
#: order, because comparing StrEnum members compares their *strings* — which
#: would make "admin" outrank "owner" alphabetically.
_ROLE_RANK: dict[MembershipRole, int] = {
    MembershipRole.OWNER: 3,
    MembershipRole.ADMIN: 2,
    MembershipRole.MEMBER: 1,
}


def role_at_least(role: MembershipRole, required: MembershipRole) -> bool:
    """Whether ``role`` satisfies ``required``.

    The single place privilege is compared, so an endpoint can never invent its
    own ordering.
    """
    return _ROLE_RANK[role] >= _ROLE_RANK[required]


class SessionStatus(StrEnum):
    ACTIVE = "active"
    #: The user logged out. Kept until expiry so that a replayed cookie can be
    #: told apart from one that simply timed out.
    REVOKED = "revoked"


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


class SubscriptionStatus(StrEnum):
    """Normalized subscription state.

    Deliberately *our* vocabulary, not Stripe's. Stripe webhooks map onto these
    values, which keeps entitlement decisions readable and means a change to
    Stripe's status set is an adapter change rather than a business-logic one.
    Stripe is never asked what a tenant is entitled to.
    """

    TRIALING = "trialing"
    ACTIVE = "active"
    #: Payment failed; access continues while retries run.
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    #: Trial elapsed with no payment method.
    EXPIRED = "expired"


#: Statuses that entitle a tenant to provision and receive calls. The billing
#: gate reads this set; nothing hardcodes a status list of its own.
ENTITLED_SUBSCRIPTION_STATUSES: frozenset[SubscriptionStatus] = frozenset(
    {
        SubscriptionStatus.TRIALING,
        SubscriptionStatus.ACTIVE,
        # Deliberate: a failed renewal must not take a customer's phone line
        # down mid-retry. Dunning handles it; the receptionist keeps answering.
        SubscriptionStatus.PAST_DUE,
    }
)


class BillingEventType(StrEnum):
    SUBSCRIPTION_CREATED = "subscription_created"
    SUBSCRIPTION_UPDATED = "subscription_updated"
    SUBSCRIPTION_CANCELED = "subscription_canceled"
    PAYMENT_SUCCEEDED = "payment_succeeded"
    PAYMENT_FAILED = "payment_failed"
    TRIAL_GRANTED = "trial_granted"
    TRIAL_EXPIRED = "trial_expired"


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class UsageKind(StrEnum):
    """What was consumed. One row per metered event, aggregated nightly."""

    CALL_MINUTES = "call_minutes"
    LLM_TOKENS = "llm_tokens"
    TTS_CHARACTERS = "tts_characters"
    PHONE_NUMBER_MONTH = "phone_number_month"


class UsageSource(StrEnum):
    """Where a usage figure came from — which decides whether it can be billed.

    An in-app measurement is an estimate: it can double-count a retry or miss a
    call the webhook never delivered. A provider-reported figure is what the
    vendor will actually invoice. Keeping them distinct is what makes nightly
    reconciliation possible, and stops an estimate from being billed as fact.
    """

    MEASURED = "measured"
    PROVIDER_REPORTED = "provider_reported"
    RECONCILED = "reconciled"
    MANUAL_ADJUSTMENT = "manual_adjustment"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class ActorType(StrEnum):
    USER = "user"
    #: A background job or workflow acting without a human.
    SYSTEM = "system"
    #: A platform operator using the admin surface.
    ADMIN = "admin"
    #: An admin acting *as* a tenant user. Always recorded distinctly from a
    #: genuine user action — an impersonated change must never be
    #: indistinguishable from one the customer made themselves.
    IMPERSONATION = "impersonation"


class AuditAction(StrEnum):
    """Every action worth reconstructing after the fact.

    Additive only: an audit log whose vocabulary changes meaning retroactively
    is worse than none, because it reads as authoritative while being wrong.
    """

    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    MAGIC_LINK_REQUESTED = "magic_link_requested"

    CONFIG_CREATED = "config_created"
    CONFIG_ACTIVATED = "config_activated"
    CONFIG_ROLLED_BACK = "config_rolled_back"

    NUMBER_PURCHASED = "number_purchased"
    NUMBER_RELEASED = "number_released"

    AGENT_CREATED = "agent_created"
    AGENT_RESYNCED = "agent_resynced"

    PROVISIONING_STARTED = "provisioning_started"
    PROVISIONING_RETRIED = "provisioning_retried"
    PROVISIONING_ABANDONED = "provisioning_abandoned"

    SUBSCRIPTION_CHANGED = "subscription_changed"
    MEMBER_INVITED = "member_invited"
    MEMBER_ROLE_CHANGED = "member_role_changed"
    MEMBER_REMOVED = "member_removed"

    ADMIN_ACTION = "admin_action"
    IMPERSONATION_STARTED = "impersonation_started"
    IMPERSONATION_ENDED = "impersonation_ended"

    DATA_EXPORT_REQUESTED = "data_export_requested"
    DATA_DELETION_REQUESTED = "data_deletion_requested"
    DATA_DELETION_COMPLETED = "data_deletion_completed"


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class NotificationKind(StrEnum):
    WELCOME = "welcome"
    PROVISIONING_COMPLETE = "provisioning_complete"
    PROVISIONING_FAILED = "provisioning_failed"
    MISSED_CALL = "missed_call"
    CALL_SUMMARY = "call_summary"
    BILLING = "billing"
    MAGIC_LINK = "magic_link"
    USAGE_WARNING = "usage_warning"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    #: Retries exhausted. Terminal, and visible in the admin panel — a
    #: notification that failed silently is how a customer finds out about a
    #: missed call from their voicemail instead of from us.
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Data lifecycle
# ---------------------------------------------------------------------------


class DeletionRequestStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
