"""Call tags: the labels a business sorts its calls by.

    GET    /tenants/{id}/agent/tags           the tags, in order
    POST   /tenants/{id}/agent/tags           add one
    PATCH  /tenants/{id}/agent/tags/{tag_id}  edit one, or switch it off
    DELETE /tenants/{id}/agent/tags/{tag_id}  remove it, and its history

Reading needs a membership; every write needs ``admin`` or ``owner`` — the same
line the rest of the Agent page draws.

The tenant always comes from the authorizing dependency, never from the path
value, and :class:`~app.services.call_tags.CallTagService` filters every query by
tenant as well — including the ones that look a tag up by its own id, so another
business's tag is a 404 rather than a leak. That matters more here than for most
configuration: a tag id is the thing the classifier is handed and the thing the
calls filter accepts, so an id that crossed a tenant boundary would cross it in
three places at once.

Unlike transfer rules and scheduled greetings, these writes publish **no**
configuration version and push nothing to the vendor: a tag is read after a call,
never during one.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.auth_deps import RequireAdmin, RequireMember
from app.core.errors import NotFoundError
from app.db.session import SessionDep
from app.models import Tenant
from app.schemas.tags import CallTagInput, CallTagsView, CallTagUpdate
from app.services.audit import Actor
from app.services.auth import Principal
from app.services.call_tags import CallTagService, tags_view_payload

router = APIRouter(prefix="/tenants", tags=["tags"])


def _tenant_of(principal: Principal) -> uuid.UUID:
    tenant_id = principal.tenant_id
    assert tenant_id is not None  # TenantAccess guarantees it
    return tenant_id


async def _load_tenant(session: SessionDep, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:  # pragma: no cover - the membership join guarantees it
        raise NotFoundError("not found")
    return tenant


async def _listing(service: CallTagService, tenant_id: uuid.UUID) -> CallTagsView:
    return CallTagsView(**tags_view_payload(await service.list(tenant_id)))


@router.get(
    "/{tenant_id}/agent/tags",
    response_model=CallTagsView,
    summary="The labels this receptionist puts on calls",
)
async def list_tags(principal: RequireMember, session: SessionDep) -> CallTagsView:
    return await _listing(CallTagService(session), _tenant_of(principal))


@router.post(
    "/{tenant_id}/agent/tags",
    response_model=CallTagsView,
    status_code=status.HTTP_201_CREATED,
    summary="Add a tag",
)
async def create_tag(
    payload: CallTagInput, principal: RequireAdmin, session: SessionDep
) -> CallTagsView:
    tenant = await _load_tenant(session, _tenant_of(principal))
    service = CallTagService(session)
    await service.create(
        tenant=tenant,
        payload=payload,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
    return await _listing(service, tenant.id)


@router.patch(
    "/{tenant_id}/agent/tags/{tag_id}",
    response_model=CallTagsView,
    summary="Edit a tag, or switch it off",
)
async def update_tag(
    tag_id: uuid.UUID,
    payload: CallTagUpdate,
    principal: RequireAdmin,
    session: SessionDep,
) -> CallTagsView:
    tenant = await _load_tenant(session, _tenant_of(principal))
    service = CallTagService(session)
    await service.update(
        tenant=tenant,
        tag_id=tag_id,
        payload=payload,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
    return await _listing(service, tenant.id)


@router.delete(
    "/{tenant_id}/agent/tags/{tag_id}",
    response_model=CallTagsView,
    summary="Remove a tag, and take it off the calls it marked",
)
async def delete_tag(
    tag_id: uuid.UUID, principal: RequireAdmin, session: SessionDep
) -> CallTagsView:
    """Returns the remaining tags rather than 204.

    Deleting a tag also removes it from every call it was ever applied to, so
    the client needs the list back to show what is left — and the UI says what
    the delete will take with it before it asks.
    """
    tenant = await _load_tenant(session, _tenant_of(principal))
    service = CallTagService(session)
    await service.delete(
        tenant=tenant,
        tag_id=tag_id,
        actor=Actor(user=principal.user, actor_type=principal.actor_type),
    )
    return await _listing(service, tenant.id)
