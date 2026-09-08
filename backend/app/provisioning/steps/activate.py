"""Step 7 — activate and tell the owner.

The tenant becomes ACTIVE and the welcome email goes out. Email is deliberately
last and deliberately *not* fatal: the receptionist is already answering by this
point, so a mail provider outage must not undo a working provisioning run. The
failure is recorded on the step instead, and the admin panel can resend.
"""

from __future__ import annotations

from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.enums import TenantStatus
from app.provisioning.context import StepContext, StepResult
from app.provisioning.steps.link_number import active_number
from app.services.notifications import NotificationService

logger = get_logger(__name__)


async def run(ctx: StepContext) -> StepResult:
    number = await active_number(ctx)

    ctx.tenant.status = TenantStatus.ACTIVE
    await ctx.session.flush()

    email_result: dict[str, object] = {"sent": False}
    try:
        sent = await NotificationService(ctx.providers.email, ctx.settings).send_activation(
            tenant=ctx.tenant, phone_e164=number.e164
        )
        email_result = {"sent": True, "message_id": sent.message_id, "provider": sent.provider}
    except AppError as exc:
        # Recorded, not raised. The tenant is live either way, and failing the
        # step here would drag a working setup back through compensation.
        logger.warning(
            "activation email failed; tenant is still active",
            extra={"tenant_id": str(ctx.tenant.id), "code": exc.code},
        )
        email_result = {"sent": False, "error": exc.code}

    logger.info(
        "tenant active",
        extra={"tenant_id": str(ctx.tenant.id), "e164": number.e164},
    )
    return StepResult(
        request={"tenant_id": str(ctx.tenant.id), "notify": ctx.tenant.contact_email},
        response={"tenant_status": TenantStatus.ACTIVE.value, "e164": number.e164, **email_result},
    )
