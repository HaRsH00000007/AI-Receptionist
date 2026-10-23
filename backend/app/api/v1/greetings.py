"""Scheduled greetings: what the receptionist says instead, and when.

    GET    /tenants/{id}/agent/greeting-rules         the rules, in order
    POST   /tenants/{id}/agent/greeting-rules         add one
    PATCH  /tenants/{id}/agent/greeting-rules/{rid}   edit one, or switch it off
    DELETE /tenants/{id}/agent/greeting-rules/{rid}   remove one

Reading needs a membership; every write needs ``admin`` or ``owner`` — the same
line the rest of the Agent page draws. A greeting is the first thing a caller
hears and it is spoken verbatim, so writing one is changing what the business
says to the public, not adjusting a display preference.

The tenant always comes from the authorizing dependency, never from the path
value, and :class:`~app.services.greeting_rules.GreetingRuleService` filters
every query by tenant as well — including the ones that look a rule up by its
own id, so another business's rule is a 404 rather than a leak.

Every write returns the whole list plus what publishing it did. A rule change
republishes the configuration and pushes the greeting that is in force now to
the voice agent; ``synced`` says whether that last part worked, and the client
shows the ``detail`` rather than inventing its own idea of what happened.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.auth_deps import RequireAdmin, RequireMember
from app.api.deps import ProvidersDep, SettingsDep
from app.core.errors import NotFoundError
from app.db.session import SessionDep
from app.models import Tenant
from app.schemas.greetings import (
    GreetingRuleInput,
    GreetingRuleResult,
    GreetingRulesView,
    GreetingRuleUpdate,
)
from app.services.audit import Actor
from app.services.auth import Principal
from app.services.greeting_rules import (
    GreetingRuleService,
    PublishOutcome,
    rules_view_payload,
)

router = APIRouter(prefix="/tenants", tags=["greetings"])


def _tenant_of(principal: Principal) -> uuid.UUID:
    tenant_id = principal.tenant_id
    assert tenant_id is not None  # TenantAccess guarantees it
    return tenant_id


async def _load_tenant(session: SessionDep, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:  # pragma: no cover - the membership join guarantees it
        raise NotFoundError("not found")
    return tenant


async def _payload(service: GreetingRuleService, tenant: Tenant) -> dict[str, object]:
    """The list as it now stands, read against this business's own clock."""
    rules = await service.list(tenant.id)
    return rules_view_payload(
        rules,
        timezone=tenant.timezone,
        default_greeting=await service.default_greeting(tenant.id),
    )


@router.get(
    "/{tenant_id}/agent/greeting-rules",
    response_model=GreetingRulesView,
    summary="The greetings that replace the usual one",
)
async def list_greeting_rules(
    principal: RequireMember,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
) -> GreetingRulesView:
    service = GreetingRuleService(session, settings, providers)
    tenant = await _load_tenant(session, _tenant_of(principal))
    return GreetingRulesView(**await _payload(service, tenant))


@router.post(
    "/{tenant_id}/agent/greeting-rules",
    response_model=GreetingRuleResult,
    status_code=status.HTTP_201_CREATED,
    summary="Add a scheduled greeting",
)
async def create_greeting_rule(
    payload: GreetingRuleInput,
    principal: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
) -> GreetingRuleResult:
    tenant = await _load_tenant(session, _tenant_of(principal))
    service = GreetingRuleService(session, settings, providers)
    _, outcome = await service.create(
        tenant=tenant,
        payload=payload,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
    return await _result(service, tenant, outcome)


@router.patch(
    "/{tenant_id}/agent/greeting-rules/{rule_id}",
    response_model=GreetingRuleResult,
    summary="Edit a scheduled greeting, or switch it off",
)
async def update_greeting_rule(
    rule_id: uuid.UUID,
    payload: GreetingRuleUpdate,
    principal: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
) -> GreetingRuleResult:
    tenant = await _load_tenant(session, _tenant_of(principal))
    service = GreetingRuleService(session, settings, providers)
    _, outcome = await service.update(
        tenant=tenant,
        rule_id=rule_id,
        payload=payload,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
    return await _result(service, tenant, outcome)


@router.delete(
    "/{tenant_id}/agent/greeting-rules/{rule_id}",
    response_model=GreetingRuleResult,
    summary="Remove a scheduled greeting",
)
async def delete_greeting_rule(
    rule_id: uuid.UUID,
    principal: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
) -> GreetingRuleResult:
    """Returns the remaining rules rather than 204.

    Deleting republishes and re-pushes, and whether that push reached the vendor
    decides which greeting callers hear next. That is not something to leave the
    client to guess from an empty body.
    """
    tenant = await _load_tenant(session, _tenant_of(principal))
    service = GreetingRuleService(session, settings, providers)
    outcome = await service.delete(
        tenant=tenant,
        rule_id=rule_id,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
    return await _result(service, tenant, outcome)


async def _result(
    service: GreetingRuleService, tenant: Tenant, outcome: PublishOutcome
) -> GreetingRuleResult:
    return GreetingRuleResult(
        **await _payload(service, tenant),
        config_version=outcome.config_version,
        synced=outcome.synced,
        detail=outcome.detail,
    )
