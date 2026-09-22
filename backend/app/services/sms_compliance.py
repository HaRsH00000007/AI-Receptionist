"""SMS compliance: a tenant's A2P 10DLC registration, and whether they may text.

The product flow is

    number assigned -> registration drafted -> submitted -> under review
        -> approved | rejected -> enabled -> campaigns

and the whole point of this module is the last arrow. :func:`can_send` is the
single answer to "may this tenant send a campaign?", and it is ``True`` only in
``ENABLED``. There is no path around it: no "approve my own registration", no
default-approved business type, no test mode that skips it. Carriers block and
fine unregistered senders, and "we assumed it would be approved" is how a
customer's number ends up on a deny list.

Who moves what:

* **The customer** edits a draft and submits it. They can re-edit and
  re-submit after a rejection. That is all.
* **Review** moves it on — ``under_review``, ``approved``, ``rejected``,
  ``enabled``. Today that is an operator recording what Twilio's Trust Hub
  said; once submission is automated it is Twilio's status callbacks. Either
  way it goes through :meth:`SmsComplianceService.record_review`, which refuses
  transitions that skip a step.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidInputError
from app.core.logging import get_logger
from app.models import PhoneNumber, Tenant
from app.models.enums import (
    ActorType,
    AuditAction,
    PhoneNumberStatus,
    SmsBrandType,
    SmsRegistrationStatus,
    TenantStatus,
)
from app.models.sms import SmsRegistration
from app.schemas.portal import (
    SmsRegistrationInput,
    SmsRegistrationView,
    SmsReviewDecision,
    SmsStateView,
)
from app.services.audit import Actor, AuditService

logger = get_logger(__name__)

#: States the customer may still edit and submit from.
EDITABLE = frozenset({SmsRegistrationStatus.DRAFT, SmsRegistrationStatus.REJECTED})

#: What review may do next, from each state. Anything else is refused.
REVIEW_TRANSITIONS: dict[SmsRegistrationStatus, frozenset[SmsRegistrationStatus]] = {
    SmsRegistrationStatus.SUBMITTED: frozenset(
        {
            SmsRegistrationStatus.UNDER_REVIEW,
            SmsRegistrationStatus.APPROVED,
            SmsRegistrationStatus.REJECTED,
        }
    ),
    SmsRegistrationStatus.UNDER_REVIEW: frozenset(
        {SmsRegistrationStatus.APPROVED, SmsRegistrationStatus.REJECTED}
    ),
    SmsRegistrationStatus.APPROVED: frozenset({SmsRegistrationStatus.ENABLED}),
}

MIN_DESCRIPTION = 40
MIN_SAMPLE = 20


def can_send(registration: SmsRegistration | None) -> bool:
    """The gate every campaign send must pass. Enabled, or nothing."""
    return registration is not None and registration.status is SmsRegistrationStatus.ENABLED


def missing_for_submission(registration: SmsRegistration) -> list[str]:
    """The fields that stop this registration being submitted, by name.

    The rules follow what carriers vet a brand and campaign on. A standard brand
    needs its EIN; a sole proprietor has none. Every campaign needs a real
    description, at least two sample messages, and an explanation of how people
    opt in — the three things a campaign is most often rejected over.
    """
    missing: list[str] = []
    required = [
        "legal_business_name",
        "address_line1",
        "city",
        "region",
        "postal_code",
        "contact_name",
        "contact_email",
        "contact_phone",
        "use_case",
    ]
    if registration.brand_type is SmsBrandType.STANDARD:
        required += ["tax_id", "website"]
    for field in required:
        if not getattr(registration, field):
            missing.append(field)

    if len((registration.campaign_description or "").strip()) < MIN_DESCRIPTION:
        missing.append("campaign_description")
    if len((registration.opt_in_description or "").strip()) < MIN_DESCRIPTION:
        missing.append("opt_in_description")

    samples = [message for message in registration.sample_messages if len(message) >= MIN_SAMPLE]
    if len(samples) < 2:
        missing.append("sample_messages")
    elif not any("stop" in message.lower() for message in samples):
        # Carriers expect opt-out language ("Reply STOP to opt out") in the
        # messages themselves; its absence is a common, avoidable rejection.
        missing.append("sample_messages_opt_out")
    return missing


def registration_view(registration: SmsRegistration) -> SmsRegistrationView:
    return SmsRegistrationView(
        status=registration.status.value,
        brand_type=registration.brand_type.value,
        legal_business_name=registration.legal_business_name,
        tax_id=registration.tax_id,
        website=registration.website,
        address_line1=registration.address_line1,
        address_line2=registration.address_line2,
        city=registration.city,
        region=registration.region,
        postal_code=registration.postal_code,
        country=registration.country,
        contact_name=registration.contact_name,
        contact_email=registration.contact_email,
        contact_phone=registration.contact_phone,
        use_case=registration.use_case.value if registration.use_case else None,
        campaign_description=registration.campaign_description,
        sample_messages=list(registration.sample_messages),
        opt_in_description=registration.opt_in_description,
        rejection_reason=registration.rejection_reason,
        submitted_at=registration.submitted_at,
        approved_at=registration.approved_at,
        enabled_at=registration.enabled_at,
    )


class SmsComplianceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def state(self, tenant: Tenant) -> SmsStateView:
        number = await self._number(tenant)
        registration = await self._registration(tenant)

        if number is None:
            state = "not_configured"
        elif registration is None:
            state = "compliance_required"
        else:
            state = registration.status.value

        return SmsStateView(
            state=state,
            phone_number=number.e164 if number else None,
            can_send=can_send(registration) and number is not None,
            editable=number is not None
            and (registration is None or registration.status in EDITABLE),
            registration=registration_view(registration) if registration else None,
        )

    async def save_draft(
        self, tenant: Tenant, payload: SmsRegistrationInput, *, actor: Actor
    ) -> SmsStateView:
        await self._require_number(tenant)
        registration = await self._registration(tenant)
        if registration is None:
            registration = SmsRegistration(tenant_id=tenant.id, sample_messages=[])
            self.session.add(registration)
        elif registration.status not in EDITABLE:
            raise ConflictError(
                "this registration is with review and can't be changed right now",
                details={"status": registration.status.value},
            )

        for field, value in payload.model_dump().items():
            setattr(registration, field, value)
        # Editing a rejected registration starts a new draft. The reason stays
        # visible until it is resubmitted, so the customer can see what to fix.
        registration.status = SmsRegistrationStatus.DRAFT
        await self.session.flush()

        self.audit.record(
            AuditAction.SMS_REGISTRATION_SAVED,
            actor_type=actor.actor_type,
            actor=actor.user,
            tenant_id=tenant.id,
            entity_type="sms_registration",
            entity_id=registration.id,
        )
        return await self.state(tenant)

    async def submit(self, tenant: Tenant, *, actor: Actor) -> SmsStateView:
        await self._require_number(tenant)
        registration = await self._registration(tenant)
        if registration is None:
            raise ConflictError("fill in your business details before submitting")
        if registration.status not in EDITABLE:
            raise ConflictError(
                "this registration has already been submitted",
                details={"status": registration.status.value},
            )
        missing = missing_for_submission(registration)
        if missing:
            raise InvalidInputError(
                "some details are missing or too short to submit",
                code="sms_registration_incomplete",
                details={"missing": missing},
            )

        now = datetime.now(UTC)
        registration.status = SmsRegistrationStatus.SUBMITTED
        registration.submitted_at = now
        registration.submitted_by_user_id = actor.user.id
        registration.rejection_reason = None

        self.audit.record(
            AuditAction.SMS_REGISTRATION_SUBMITTED,
            actor_type=actor.actor_type,
            actor=actor.user,
            tenant_id=tenant.id,
            entity_type="sms_registration",
            entity_id=registration.id,
            meta={"brand_type": registration.brand_type.value},
        )
        logger.info("sms registration submitted", extra={"tenant_id": str(tenant.id)})
        return await self.state(tenant)

    async def record_review(self, tenant: Tenant, decision: SmsReviewDecision) -> SmsStateView:
        """Record what review decided. Operator-only; refuses skipped steps."""
        registration = await self._registration(tenant)
        if registration is None:
            raise ConflictError("this tenant has no SMS registration")

        allowed = REVIEW_TRANSITIONS.get(registration.status, frozenset())
        if decision.status not in allowed:
            raise ConflictError(
                "that review outcome doesn't follow from the current status",
                details={
                    "from": registration.status.value,
                    "to": decision.status.value,
                    "allowed": sorted(status.value for status in allowed),
                },
            )
        if decision.status is SmsRegistrationStatus.REJECTED and not decision.rejection_reason:
            # A rejection the customer cannot act on is a dead end.
            raise InvalidInputError("say why it was rejected, so the customer can fix it")
        if decision.status is SmsRegistrationStatus.ENABLED:
            # Enabled means the number is attached to the registered messaging
            # service; without that id, "enabled" would be a claim, not a fact.
            messaging_service = (
                decision.twilio_messaging_service_sid or registration.twilio_messaging_service_sid
            )
            if not messaging_service:
                raise InvalidInputError(
                    "enabling needs the messaging service the number was attached to"
                )

        now = datetime.now(UTC)
        registration.status = decision.status
        registration.reviewed_at = now
        registration.rejection_reason = (
            decision.rejection_reason if decision.status is SmsRegistrationStatus.REJECTED else None
        )
        if decision.status is SmsRegistrationStatus.APPROVED:
            registration.approved_at = now
        if decision.status is SmsRegistrationStatus.ENABLED:
            registration.enabled_at = now
        for field in (
            "twilio_customer_profile_sid",
            "twilio_brand_sid",
            "twilio_campaign_sid",
            "twilio_messaging_service_sid",
        ):
            value = getattr(decision, field)
            if value:
                setattr(registration, field, value)

        self.audit.record(
            AuditAction.SMS_REGISTRATION_REVIEWED,
            actor_type=ActorType.ADMIN,
            actor_label="operator",
            tenant_id=tenant.id,
            entity_type="sms_registration",
            entity_id=registration.id,
            meta={"status": decision.status.value},
        )
        return await self.state(tenant)

    # ---- lookups ---------------------------------------------------------
    async def _registration(self, tenant: Tenant) -> SmsRegistration | None:
        return (
            await self.session.execute(
                select(SmsRegistration).where(SmsRegistration.tenant_id == tenant.id)
            )
        ).scalar_one_or_none()

    async def _number(self, tenant: Tenant) -> PhoneNumber | None:
        if tenant.status is not TenantStatus.ACTIVE:
            return None
        return (
            await self.session.execute(
                select(PhoneNumber).where(
                    PhoneNumber.tenant_id == tenant.id,
                    PhoneNumber.status == PhoneNumberStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()

    async def _require_number(self, tenant: Tenant) -> None:
        if await self._number(tenant) is None:
            raise ConflictError("SMS can be set up once your phone number is live")
