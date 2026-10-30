"""Step 7 — activate and tell the owner.

The tenant becomes ACTIVE and the welcome email is **queued**. Queued, not
sent: this step used to call the email provider inline and swallow any
``AppError`` into a warning, which meant a vendor refusal left no durable trace,
no retry and nothing in the admin panel — and this docstring used to claim the
admin panel could resend it, which it could not, because no row existed. A
tenant could go live and its owner never learn the phone number.

The email is still deliberately not fatal. Enqueuing is a local insert in the
transaction that activated the tenant, so the only way it fails is a database
failure that would have failed the step anyway; there is no vendor in the path
to be down. Delivery, retries and the eventual give-up are the outbox's job.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.enums import TenantStatus
from app.provisioning.context import StepContext, StepResult
from app.provisioning.steps.link_number import active_number
from app.services.tenant_notifications import stage_activation

logger = get_logger(__name__)


async def run(ctx: StepContext) -> StepResult:
    number = await active_number(ctx)

    ctx.tenant.status = TenantStatus.ACTIVE
    await ctx.session.flush()

    queued = await stage_activation(
        ctx.session, ctx.settings, tenant=ctx.tenant, phone_e164=number.e164
    )

    logger.info(
        "tenant active",
        extra={"tenant_id": str(ctx.tenant.id), "e164": number.e164},
    )
    return StepResult(
        request={"tenant_id": str(ctx.tenant.id), "notify": ctx.tenant.contact_email},
        response={
            "tenant_status": TenantStatus.ACTIVE.value,
            "e164": number.e164,
            # `None` means an identical notification was already queued by an
            # earlier attempt at this step, which is a success.
            "notification_queued": queued is not None,
            "notification_id": str(queued.id) if queued is not None else None,
        },
    )
