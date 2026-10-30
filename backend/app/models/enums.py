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


class AuthTokenPurpose(StrEnum):
    """What a single-use credential in ``auth_tokens`` is for.

    Kept apart from :class:`SessionStatus` and the ``sessions`` table on
    purpose. A magic link *becomes* a session and so belongs there; these two
    never do — a reset token that could be presented as a bearer token would be
    a sign-in that skipped the password it was about to set.
    """

    PASSWORD_RESET = "password_reset"
    EMAIL_VERIFICATION = "email_verification"


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
    RECEPTIONIST_DELETED = "receptionist_deleted"
    CONTACT_CREATED = "contact_created"
    CONTACT_UPDATED = "contact_updated"
    CONTACT_DELETED = "contact_deleted"
    CONTACT_BLOCKED = "contact_blocked"
    CONTACT_UNBLOCKED = "contact_unblocked"
    CONTACTS_IMPORTED = "contacts_imported"
    TAG_CREATED = "tag_created"
    TAG_UPDATED = "tag_updated"
    TAG_DELETED = "tag_deleted"
    TRANSFER_RULE_CREATED = "transfer_rule_created"
    TRANSFER_RULE_UPDATED = "transfer_rule_updated"
    TRANSFER_RULE_DELETED = "transfer_rule_deleted"
    GREETING_RULE_CREATED = "greeting_rule_created"
    GREETING_RULE_UPDATED = "greeting_rule_updated"
    GREETING_RULE_DELETED = "greeting_rule_deleted"

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

    # ---- customer portal ----
    PROFILE_UPDATED = "profile_updated"
    ACCOUNT_UPDATED = "account_updated"
    PASSWORD_CHANGED = "password_changed"
    ACCOUNT_REGISTERED = "account_registered"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    EMAIL_VERIFICATION_REQUESTED = "email_verification_requested"
    EMAIL_VERIFIED = "email_verified"
    BUSINESS_CREATED = "business_created"
    INTEGRATION_CONNECTED = "integration_connected"
    INTEGRATION_DISCONNECTED = "integration_disconnected"
    SMS_REGISTRATION_SAVED = "sms_registration_saved"
    SMS_REGISTRATION_SUBMITTED = "sms_registration_submitted"
    SMS_REGISTRATION_REVIEWED = "sms_registration_reviewed"


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
    PASSWORD_RESET = "password_reset"
    EMAIL_VERIFICATION = "email_verification"


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


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------


class IntegrationProvider(StrEnum):
    """Every third-party service a tenant can connect.

    A member here is an *identity*, not a promise that the integration works:
    whether a provider can actually be connected is decided by the catalog
    (``app.integrations.catalog``), from the implementation and the operator's
    configuration.
    """

    GOOGLE_CALENDAR = "google_calendar"
    MICROSOFT_OUTLOOK = "microsoft_outlook"
    ZAPIER = "zapier"
    CLIO = "clio"
    ACUITY_SCHEDULING = "acuity_scheduling"


class IntegrationStatus(StrEnum):
    #: Credentials are stored and were valid when last used.
    CONNECTED = "connected"
    #: Credentials are stored but a refresh or call was refused — typically the
    #: customer revoked access at the provider. Reconnecting fixes it.
    ERROR = "error"
    #: Was connected; credentials have been deleted. Kept for the audit trail.
    DISCONNECTED = "disconnected"


# ---------------------------------------------------------------------------
# SMS compliance
# ---------------------------------------------------------------------------


class ContactStatus(StrEnum):
    """Whether a contact is someone the business still wants to hear from."""

    ACTIVE = "active"
    #: The business does not want this caller. What blocking *does* is a
    #: product decision, not an implied one: today it marks the record and
    #: filters the list. It does not drop calls, because silently hanging up on
    #: a caller is a thing a business should opt into explicitly.
    BLOCKED = "blocked"


class ContactSource(StrEnum):
    """Where a contact came from, which decides what may be overwritten.

    A name a person typed is never replaced by a name a caller claimed on the
    phone; the reverse is allowed.
    """

    MANUAL = "manual"
    CALL = "call"
    IMPORT = "import"


class Responsiveness(StrEnum):
    """How quickly the receptionist decides a caller has finished speaking.

    Three values because the vendor has three. ElevenLabs' ``turn_eagerness``
    accepts ``patient``, ``normal`` or ``eager`` and refuses anything else —
    checked against the live API — so a fourth setting here would be two names
    for one behaviour, and a customer choosing between them would be choosing
    nothing.

    ``AUTO`` is ``normal``: the vendor's own recommendation, and what every
    receptionist got before this existed.
    """

    AUTO = "auto"
    #: Waits longer before answering. For callers who pause mid-sentence.
    LOW = "low"
    #: Answers sooner, at the cost of interrupting a caller who paused.
    HIGH = "high"

    @property
    def eagerness(self) -> str:
        """The vendor's word for it."""
        return {"auto": "normal", "low": "patient", "high": "eager"}[self.value]


class TagColor(StrEnum):
    """The swatch beside a tag's name.

    A closed set rather than a free hex value, for the same reason every other
    colour in this product is a token: a customer-chosen ``#00ff00`` is
    unreadable on one of the two themes, and nobody picking a colour in a modal
    is checking it against a dark background. Each maps to a palette variable
    the client already has.
    """

    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    BLUE = "blue"
    VIOLET = "violet"
    TEAL = "teal"
    INDIGO = "indigo"
    SLATE = "slate"


class TransferType(StrEnum):
    """How a live call is handed to a person.

    Two words a business understands, mapped to the vendor's own names where
    the call is actually handed over (``app.providers.real.voice``):

    * ``WARM`` — the person is dialled, told who is calling and why, and only
      then joined to the caller. ElevenLabs calls this a *conference* transfer.
    * ``COLD`` — the call goes straight through with the caller's own number as
      the caller ID, and nobody is briefed. ElevenLabs calls this *blind*.

    SIP REFER, the vendor's third type, is deliberately absent: it needs a SIP
    trunk that permits REFER, and this product hands every number to ElevenLabs
    through Twilio.
    """

    WARM = "warm"
    COLD = "cold"


class GreetingScheduleKind(StrEnum):
    """Which calendar a scheduled greeting is read against.

    Two kinds because businesses describe their exceptions in two different
    ways, and collapsing them into one would make the common cases awkward:

    * ``WEEKLY`` — "every weekday between noon and one". Stored as weekdays
      plus a window of minutes, and it repeats forever.
    * ``DATED`` — "the 24th to the 26th of December". Stored as a date range,
      and it stops being true once the dates pass.

    A dated rule outranks a weekly one when both match, because a one-off
    closure is the more specific statement; see
    :mod:`app.services.greeting_schedule`.
    """

    WEEKLY = "weekly"
    DATED = "dated"


class WebsiteOnboardingStatus(StrEnum):
    """Where a website-based setup has got to.

    A crawl and an LLM call are slow and can fail, so this is a job status, not
    a form state: the customer can close the tab and come back to it.

    ``READY`` means there is knowledge to review — never that a receptionist
    exists. Nothing is provisioned, bought or created at a vendor until the
    customer confirms, which moves the job to ``CONFIRMED``.
    """

    #: Queued. The worker has not picked it up yet.
    PENDING = "pending"
    #: Reading the website.
    CRAWLING = "crawling"
    #: Crawled; asking the LLM to structure what was found.
    GENERATING = "generating"
    #: There is a draft to review and edit.
    READY = "ready"
    #: The crawl or the generation failed. Retryable by the customer.
    FAILED = "failed"
    #: The customer confirmed and a business was created from this draft.
    CONFIRMED = "confirmed"


#: Statuses the worker should pick up. ``READY``, ``FAILED`` and ``CONFIRMED``
#: are all waiting on a person, not on us.
WEBSITE_ONBOARDING_CLAIMABLE = (
    WebsiteOnboardingStatus.PENDING,
    WebsiteOnboardingStatus.CRAWLING,
    WebsiteOnboardingStatus.GENERATING,
)


class SmsRegistrationStatus(StrEnum):
    """Where a tenant's A2P 10DLC registration stands.

    US carriers refuse application-to-person texts from a 10-digit number that
    is not registered to a vetted brand and campaign. Only the *customer*
    moves a registration from draft to submitted; everything after that is
    decided by review — ours and then the carriers' via Twilio — and is never
    something the customer can set.

    "Not configured" and "compliance required" are not stored states: they are
    read off the tenant (no number yet / number but no registration row).
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    #: Approved *and* the number is attached to the registered messaging
    #: service. The only state in which a campaign may be sent.
    ENABLED = "enabled"


class SmsBrandType(StrEnum):
    #: A registered business with a tax id (EIN). Higher throughput.
    STANDARD = "standard"
    #: No EIN. Carriers cap volume and allow one number.
    SOLE_PROPRIETOR = "sole_proprietor"


class SmsUseCase(StrEnum):
    """The campaign use case, as carriers classify it. A subset of TCR's list —
    the ones a receptionist business plausibly needs."""

    CUSTOMER_CARE = "customer_care"
    ACCOUNT_NOTIFICATION = "account_notification"
    APPOINTMENT_REMINDERS = "appointment_reminders"
    MARKETING = "marketing"
    MIXED = "mixed"
