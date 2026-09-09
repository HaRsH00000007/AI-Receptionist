"""Step 3 — the money gate.

Sits immediately before ``purchase_number``, the only step that spends real
money. Its whole job is to refuse.

The POC had no such step: every anonymous form submission reached Twilio and
bought a number, a recurring monthly charge, with nothing checking whether the
business had paid or was even real. `docs/02_PLAN_PRODUCTION.md` §4 calls that
"the single biggest leak".

**Refusing here parks the run; it does not fail it.** A tenant who has not paid
is not a broken tenant — they are a tenant who has not paid yet. Failing the run
would trigger saga compensation and release resources for someone who is about
to become a customer. So this step raises
:class:`~app.core.errors.BillingBlockedError`, which the engine handles by
moving the run to ``BILLING_BLOCKED`` and re-checking later; a Stripe webhook
granting entitlement un-parks it immediately.

This step is *not* the only defence. ``purchase_number`` re-evaluates the same
gate immediately before calling Twilio, because entitlement can change in the
window between the two steps. See that module for why one check is not enough.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.provisioning.context import StepContext, StepResult
from app.services.billing_gate import require_entitlement

logger = get_logger(__name__)


async def run(ctx: StepContext) -> StepResult:
    # Raises BillingBlockedError when the tenant is not entitled. Nothing below
    # this line executes in that case, and no vendor is contacted.
    decision = await require_entitlement(ctx.session, ctx.settings, ctx.tenant.id)

    logger.info(
        "billing gate authorized provisioning",
        extra={
            "tenant_id": str(ctx.tenant.id),
            "plan": decision.plan.value if decision.plan else None,
            "reason": decision.reason,
        },
    )

    return StepResult(
        request={
            "tenant_id": str(ctx.tenant.id),
            "gate_enabled": ctx.settings.billing_gate_enabled,
        },
        # No Stripe identifiers here. The step row is shown in the admin panel
        # and carried in API responses; a customer id is a processor handle that
        # nothing outside the billing service has any use for.
        response=decision.as_metadata(),
    )
