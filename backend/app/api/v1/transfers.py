"""Transfer rules: where a call goes when it should go to a person.

    GET    /tenants/{id}/agent/transfer-rules         the rules, in order
    POST   /tenants/{id}/agent/transfer-rules         add one
    PATCH  /tenants/{id}/agent/transfer-rules/{rid}   edit or switch one off
    DELETE /tenants/{id}/agent/transfer-rules/{rid}   remove one

Reading needs a membership; every write needs ``admin`` or ``owner``. The line
is drawn harder here than elsewhere in the portal for a reason worth stating: a
transfer rule is the one piece of customer-editable configuration that makes the
product dial a number. A member who can read the dashboard has no business
pointing a receptionist at a phone.

The tenant always comes from the authorizing dependency, never from the path
value, and :class:`~app.services.transfer_rules.TransferRuleService` filters
every query by tenant as well — including the ones that look a rule up by its
own id, so another business's rule is a 404 rather than a leak.

Every write returns the whole list plus what publishing it did. A rule change
republishes the configuration and pushes it to the voice agent, and ``synced``
says whether that last part worked; the client shows the ``detail`` rather than
inventing its own idea of what happened.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.auth_deps import RequireAdmin, RequireMember
from app.api.deps import ProvidersDep, SettingsDep
from app.core.errors import NotFoundError
from app.db.session import SessionDep
from app.models import Tenant
from app.schemas.transfers import (
    TransferRuleInput,
    TransferRuleResult,
    TransferRulesView,
    TransferRuleUpdate,
)
from app.services.audit import Actor
from app.services.auth import Principal
from app.services.transfer_rules import (
    PublishOutcome,
    TransferRuleService,
    rules_view_payload,
)

router = APIRouter(prefix="/tenants", tags=["transfers"])


def _tenant_of(principal: Principal) -> uuid.UUID:
    tenant_id = principal.tenant_id
    assert tenant_id is not None  # TenantAccess guarantees it
    return tenant_id


async def _load_tenant(session: SessionDep, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:  # pragma: no cover - the membership join guarantees it
        raise NotFoundError("not found")
    return tenant


async def _result(
    service: TransferRuleService, tenant: Tenant, outcome: PublishOutcome
) -> TransferRuleResult:
    """The list as it now stands, with what publishing it did."""
    rules = await service.list(tenant.id)
    return TransferRuleResult(
        **rules_view_payload(rules, cold_available=service.cold_transfer_available),
        config_version=outcome.config_version,
        synced=outcome.synced,
        detail=outcome.detail,
    )


@router.get(
    "/{tenant_id}/agent/transfer-rules",
    response_model=TransferRulesView,
    summary="The rules that send a call to a person",
)
async def list_transfer_rules(
    principal: RequireMember,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
) -> TransferRulesView:
    service = TransferRuleService(session, settings, providers)
    rules = await service.list(_tenant_of(principal))
    return TransferRulesView(
        **rules_view_payload(rules, cold_available=service.cold_transfer_available)
    )


@router.post(
    "/{tenant_id}/agent/transfer-rules",
    response_model=TransferRuleResult,
    status_code=status.HTTP_201_CREATED,
    summary="Add a transfer rule",
)
async def create_transfer_rule(
    payload: TransferRuleInput,
    principal: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
) -> TransferRuleResult:
    tenant = await _load_tenant(session, _tenant_of(principal))
    service = TransferRuleService(session, settings, providers)
    _, outcome = await service.create(
        tenant=tenant,
        payload=payload,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
    return await _result(service, tenant, outcome)


@router.patch(
    "/{tenant_id}/agent/transfer-rules/{rule_id}",
    response_model=TransferRuleResult,
    summary="Edit a transfer rule, or switch it off",
)
async def update_transfer_rule(
    rule_id: uuid.UUID,
    payload: TransferRuleUpdate,
    principal: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
) -> TransferRuleResult:
    tenant = await _load_tenant(session, _tenant_of(principal))
    service = TransferRuleService(session, settings, providers)
    _, outcome = await service.update(
        tenant=tenant,
        rule_id=rule_id,
        payload=payload,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
    return await _result(service, tenant, outcome)


@router.delete(
    "/{tenant_id}/agent/transfer-rules/{rule_id}",
    response_model=TransferRuleResult,
    summary="Remove a transfer rule",
)
async def delete_transfer_rule(
    rule_id: uuid.UUID,
    principal: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
) -> TransferRuleResult:
    """Returns the remaining rules rather than 204.

    Deleting republishes and re-pushes, and whether that push reached the
    vendor decides whether callers are *still being transferred*. That is not
    something to leave the client to guess from an empty body.
    """
    tenant = await _load_tenant(session, _tenant_of(principal))
    service = TransferRuleService(session, settings, providers)
    outcome = await service.delete(
        tenant=tenant,
        rule_id=rule_id,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
    return await _result(service, tenant, outcome)
