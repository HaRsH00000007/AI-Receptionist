"""Step 1 — validate and normalize.

Cheap, local, and first, so that a signup that can never succeed fails before
anything is bought. Everything here is deterministic; nothing external is called.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.errors import InvalidInputError
from app.data.area_codes import lookup
from app.models import BusinessProfile
from app.provisioning.context import StepContext, StepResult
from app.services.normalization import normalize_email, normalize_phone


async def run(ctx: StepContext) -> StepResult:
    tenant = ctx.tenant

    profile = (
        await ctx.session.execute(
            select(BusinessProfile).where(BusinessProfile.tenant_id == tenant.id)
        )
    ).scalar_one_or_none()
    if profile is None:
        # Terminal: a tenant without a profile cannot be provisioned, and no
        # number of retries will conjure one.
        raise InvalidInputError(
            "tenant has no business profile", details={"tenant_id": str(tenant.id)}
        )

    if not profile.services:
        raise InvalidInputError(
            "at least one service is required", details={"tenant_id": str(tenant.id)}
        )

    # Re-normalize rather than trust: rows can be edited by an admin or arrive
    # from an importer that never went through the signup endpoint.
    tenant.contact_email = normalize_email(tenant.contact_email)
    tenant.contact_phone = normalize_phone(tenant.contact_phone)

    area_code = tenant.area_code
    if not area_code:
        raise InvalidInputError(
            "an area code is required to buy a number", details={"tenant_id": str(tenant.id)}
        )

    info = lookup(area_code)
    tenant.timezone = info.timezone

    return StepResult(
        request={"tenant_id": str(tenant.id), "area_code": area_code},
        response={
            "timezone": info.timezone,
            "state": info.state,
            "services": len(profile.services),
            "hours_prenormalized": profile.hours_json is not None,
        },
    )
