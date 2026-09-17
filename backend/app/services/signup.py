"""Signup ingress.

Turns a validated form into a tenant, a business profile, a provisioning run and
its seven pending steps — in one transaction — and stops there. No provider is
called: signup returns in milliseconds and the worker does the slow, failure-prone
part, which is what lets the form stay up when Twilio is down.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.data.area_codes import timezone_for_area_code
from app.models import BusinessProfile, ProvisioningRun, ProvisioningStepRecord, Tenant
from app.models.enums import (
    STEP_SEQUENCE,
    ProvisioningStatus,
    StepStatus,
    TenantStatus,
)
from app.schemas.business import EscalationPolicy, dump_json_column
from app.schemas.signup import SignupRequest
from app.services.billing import BillingService
from app.services.idempotency import step_idempotency_key
from app.services.normalization import (
    normalize_area_code,
    normalize_email,
    normalize_phone,
    normalize_services,
    parse_opening_hours,
)

logger = get_logger(__name__)

#: Statuses in which a tenant still holds its email address. Mirrors the partial
#: unique index `uq_tenants_live_contact_email`; a cancelled tenant frees it.
LIVE_TENANT_STATUSES = (TenantStatus.PENDING, TenantStatus.ACTIVE)


@dataclass(frozen=True, slots=True)
class SignupResult:
    tenant: Tenant
    run: ProvisioningRun
    #: ``False`` when an existing live signup was returned instead.
    created: bool


class SignupService:
    """Creates the tenant and schedules its provisioning."""

    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        # Optional so existing callers keep working; when absent the trial is
        # not granted here and the money gate parks the run until billing says
        # yes. Failing closed is the right default for a money decision.
        self.settings = settings

    async def submit(self, request: SignupRequest, *, correlation_id: str) -> SignupResult:
        """Accept a signup, or return the existing one for this business.

        Duplicate submissions are the normal case, not an error: people
        double-click, and forms get resubmitted. Returning the existing tenant
        makes the endpoint idempotent, and the database's partial unique index
        makes that true even when two requests race.
        """
        email = normalize_email(str(request.notification_email))
        phone = normalize_phone(request.contact_phone)
        area_code = normalize_area_code(request.area_code)
        timezone = timezone_for_area_code(area_code)

        existing = await self._find_live_tenant(email)
        if existing is not None:
            run = await self._latest_run(existing)
            logger.info(
                "signup matched an existing live tenant",
                extra={"tenant_id": str(existing.id), "email": email},
            )
            return SignupResult(tenant=existing, run=run, created=False)

        tenant = Tenant(
            name=request.business_name,
            business_type=request.business_type,
            contact_email=email,
            contact_phone=phone,
            area_code=area_code,
            timezone=timezone,
            plan=request.plan,
            status=TenantStatus.PENDING,
        )
        profile = self._build_profile(request, tenant, timezone)
        run = self._build_run(tenant, correlation_id)

        self.session.add_all([tenant, profile, run, *self._build_steps(run)])

        try:
            await self.session.flush()
        except IntegrityError:
            # Two submissions raced and the index rejected the loser. The winner
            # is durable, so re-read it rather than surfacing a 500.
            await self.session.rollback()
            winner = await self._find_live_tenant(email)
            if winner is None:
                raise
            logger.info(
                "signup lost a race and adopted the winning tenant",
                extra={"tenant_id": str(winner.id), "email": email},
            )
            return SignupResult(tenant=winner, run=await self._latest_run(winner), created=False)

        # Granted after the tenant is durable and inside the same transaction,
        # so a signup either produces a tenant *and* its entitlement or neither.
        # A tenant with no subscription is parked by the money gate immediately,
        # which is safe but indistinguishable from a bug.
        if self.settings is not None:
            await BillingService(self.session, self.settings).grant_trial(tenant)

        logger.info(
            "signup accepted",
            extra={
                "tenant_id": str(tenant.id),
                "run_id": str(run.id),
                "business_type": tenant.business_type.value,
                "plan": tenant.plan.value,
                "area_code": area_code,
                "timezone": timezone,
            },
        )
        return SignupResult(tenant=tenant, run=run, created=True)

    # ---- construction ----------------------------------------------------
    def _build_profile(
        self, request: SignupRequest, tenant: Tenant, timezone: str
    ) -> BusinessProfile:
        services = normalize_services(request.services)
        hours = parse_opening_hours(request.operating_hours, timezone=timezone)

        return BusinessProfile(
            tenant_id=tenant.id,
            # The submitted form, kept verbatim. When a template improves we
            # re-derive from this rather than from a previous interpretation.
            raw_form_json=request.model_dump(mode="json"),
            services=services,
            hours_raw=request.operating_hours,
            # None when the text did not match a known pattern; the config step
            # then asks the LLM. Never a guess.
            hours_json=dump_json_column(hours) if hours else None,
            greeting_style=request.greeting_style,
            # Empty means the owner asked us to write one; NULL records that
            # rather than storing a blank line the agent would try to say.
            greeting_custom=request.custom_greeting or None,
            escalation_raw=request.escalation_rules or None,
            escalation_json=(
                dump_json_column(EscalationPolicy(notify_email=tenant.contact_email))
                if not request.escalation_rules
                else None
            ),
            config_version=1,
        )

    def _build_run(self, tenant: Tenant, correlation_id: str) -> ProvisioningRun:
        return ProvisioningRun(
            tenant_id=tenant.id,
            status=ProvisioningStatus.DRAFT,
            correlation_id=correlation_id,
            attempt=0,
            # Eligible for the worker's very next poll.
            next_attempt_at=None,
        )

    def _build_steps(self, run: ProvisioningRun) -> list[ProvisioningStepRecord]:
        """Write all seven steps up front, PENDING.

        The status page and the admin panel can then show the whole plan from
        the first second, instead of a list that grows as things happen — which
        makes "stuck at step 3 of 7" answerable without reading the code.
        """
        return [
            ProvisioningStepRecord(
                run_id=run.id,
                step_name=step,
                status=StepStatus.PENDING,
                idempotency_key=step_idempotency_key(run.id, step),
                attempt=0,
            )
            for step in STEP_SEQUENCE
        ]

    # ---- lookups ---------------------------------------------------------
    async def _find_live_tenant(self, email: str) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant)
            .where(Tenant.contact_email == email)
            .where(Tenant.status.in_(LIVE_TENANT_STATUSES))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _latest_run(self, tenant: Tenant) -> ProvisioningRun:
        result = await self.session.execute(
            select(ProvisioningRun)
            .where(ProvisioningRun.tenant_id == tenant.id)
            .order_by(ProvisioningRun.created_at.desc())
            .limit(1)
        )
        run = result.scalar_one_or_none()
        if run is not None:
            return run
        # A live tenant with no run means an earlier attempt half-failed. Give
        # it one so the worker can pick the tenant up rather than stranding it.
        replacement = self._build_run(tenant, tenant.id.hex)
        self.session.add_all([replacement, *self._build_steps(replacement)])
        await self.session.flush()
        return replacement
