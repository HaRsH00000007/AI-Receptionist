"""The contacts a business keeps on file.

    GET    /tenants/{id}/contacts          list, search, filter, paginate
    POST   /tenants/{id}/contacts          add one by hand
    GET    /tenants/{id}/contacts/{cid}    one contact, with its call history
    PATCH  /tenants/{id}/contacts/{cid}    edit name, number or note
    DELETE /tenants/{id}/contacts/{cid}    remove it (call history is kept)
    POST   /tenants/{id}/contacts/{cid}/block     block or unblock
    POST   /tenants/{id}/contacts/import   upload a CSV
    GET    /tenants/{id}/contacts/export   download a CSV

Reading needs a membership; every write needs ``admin`` or ``owner``, the same
line the rest of the portal draws — a member can see who called, but changing
the business's own records is for the people who run it.

The tenant always comes from the authorizing dependency, never from the path
value directly, so a contact belonging to another business is a 404 rather than
a leak. ``ContactService`` filters every query by tenant as well; the two are
deliberately redundant.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from app.api.auth_deps import RequireAdmin, RequireMember
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models.enums import AuditAction, ContactStatus
from app.schemas.contacts import (
    ContactCreate,
    ContactImportResult,
    ContactListView,
    ContactUpdate,
    ContactView,
)
from app.services.audit import AuditService
from app.services.auth import Principal
from app.services.contacts import ContactPage, ContactRecord, ContactService

logger = get_logger(__name__)

router = APIRouter(prefix="/tenants", tags=["contacts"])


def _tenant_of(principal: Principal) -> uuid.UUID:
    tenant_id = principal.tenant_id
    assert tenant_id is not None  # TenantAccess guarantees it
    return tenant_id


def _view(record: ContactRecord) -> ContactView:
    contact = record.contact
    return ContactView(
        id=contact.id,
        phone_e164=contact.phone_e164,
        full_name=contact.full_name,
        note=contact.note,
        status=contact.status,
        source=contact.source,
        created_at=contact.created_at,
        blocked_at=contact.blocked_at,
        call_count=record.call_count,
        first_call_at=record.first_call_at,
        last_call_at=record.last_call_at,
    )


def _page(page: ContactPage, *, limit: int, offset: int) -> ContactListView:
    return ContactListView(
        contacts=[_view(record) for record in page.records],
        total=page.total,
        active_count=page.active_count,
        blocked_count=page.blocked_count,
        limit=limit,
        offset=offset,
    )


def _status(value: str) -> ContactStatus | None:
    """``all`` means both tabs; anything else must be a real status."""
    return None if value == "all" else ContactStatus(value)


@router.get(
    "/{tenant_id}/contacts",
    response_model=ContactListView,
    summary="The contacts on file, searchable and filtered",
)
async def list_contacts(
    principal: RequireMember,
    session: SessionDep,
    search: str = Query(default="", max_length=120),
    contact_status: str = Query(default="active", pattern="^(active|blocked|all)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ContactListView:
    """Both tab counts come back on every page, so the tabs stay accurate
    without a second request."""
    page = await ContactService(session).list(
        tenant_id=_tenant_of(principal),
        status=_status(contact_status),
        search=search,
        limit=limit,
        offset=offset,
    )
    return _page(page, limit=limit, offset=offset)


@router.post(
    "/{tenant_id}/contacts",
    response_model=ContactView,
    status_code=status.HTTP_201_CREATED,
    summary="Add a contact",
)
async def create_contact(
    payload: ContactCreate,
    principal: RequireAdmin,
    session: SessionDep,
) -> ContactView:
    tenant_id = _tenant_of(principal)
    service = ContactService(session)
    contact = await service.create(
        tenant_id=tenant_id,
        phone=payload.phone_number,
        full_name=payload.full_name,
        note=payload.note,
    )
    AuditService(session).record(
        AuditAction.CONTACT_CREATED,
        actor_type=principal.actor_type,
        actor=principal.user,
        tenant_id=tenant_id,
        entity_type="contact",
        entity_id=contact.id,
        # The number is the contact's identity, so it belongs in the trail; the
        # note does not, and is deliberately absent.
        meta={"phone_e164": contact.phone_e164, "source": contact.source.value},
    )
    return _view(await service.get(tenant_id=tenant_id, contact_id=contact.id))


@router.get(
    "/{tenant_id}/contacts/export",
    response_class=Response,
    summary="Download the current list as CSV",
)
async def export_contacts(
    principal: RequireMember,
    session: SessionDep,
    search: str = Query(default="", max_length=120),
    contact_status: str = Query(default="active", pattern="^(active|blocked|all)$"),
) -> Response:
    """Exports exactly what the list is showing — same filter, same search —
    so what downloads matches what was on screen."""
    csv_text = await ContactService(session).export_csv(
        tenant_id=_tenant_of(principal), status=_status(contact_status), search=search
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": 'attachment; filename="contacts.csv"'},
    )


@router.post(
    "/{tenant_id}/contacts/import",
    response_model=ContactImportResult,
    summary="Add contacts from a CSV file",
)
async def import_contacts(
    principal: RequireAdmin,
    session: SessionDep,
    file: UploadFile = File(description="CSV with a phone number column"),
) -> ContactImportResult:
    """Reports every row it could not take, rather than dropping them quietly."""
    tenant_id = _tenant_of(principal)
    content = await file.read()
    report = await ContactService(session).import_csv(tenant_id=tenant_id, content=content)
    AuditService(session).record(
        AuditAction.CONTACTS_IMPORTED,
        actor_type=principal.actor_type,
        actor=principal.user,
        tenant_id=tenant_id,
        entity_type="tenant",
        entity_id=tenant_id,
        meta={
            "imported": report.imported,
            "updated": report.updated,
            "skipped": report.skipped_duplicate,
            "invalid": report.invalid,
        },
    )
    return ContactImportResult(
        imported=report.imported,
        updated=report.updated,
        skipped_duplicate=report.skipped_duplicate,
        invalid=report.invalid,
        errors=[{"row": row, "reason": reason} for row, reason in report.errors],
    )


@router.get(
    "/{tenant_id}/contacts/{contact_id}",
    response_model=ContactView,
    summary="One contact, with its call history",
)
async def get_contact(
    contact_id: uuid.UUID,
    principal: RequireMember,
    session: SessionDep,
) -> ContactView:
    record = await ContactService(session).get(
        tenant_id=_tenant_of(principal), contact_id=contact_id
    )
    return _view(record)


@router.patch(
    "/{tenant_id}/contacts/{contact_id}",
    response_model=ContactView,
    summary="Edit a contact",
)
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    principal: RequireAdmin,
    session: SessionDep,
) -> ContactView:
    tenant_id = _tenant_of(principal)
    service = ContactService(session)
    contact = await service.update(
        tenant_id=tenant_id,
        contact_id=contact_id,
        phone=payload.phone_number,
        full_name=payload.full_name,
        note=payload.note,
    )
    AuditService(session).record(
        AuditAction.CONTACT_UPDATED,
        actor_type=principal.actor_type,
        actor=principal.user,
        tenant_id=tenant_id,
        entity_type="contact",
        entity_id=contact.id,
        meta={"phone_e164": contact.phone_e164},
    )
    return _view(await service.get(tenant_id=tenant_id, contact_id=contact.id))


@router.delete(
    "/{tenant_id}/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a contact",
)
async def delete_contact(
    contact_id: uuid.UUID,
    principal: RequireAdmin,
    session: SessionDep,
) -> None:
    """The contact goes; the calls they made stay. Deleting a contact is
    tidying an address book, not erasing history."""
    tenant_id = _tenant_of(principal)
    await ContactService(session).delete(tenant_id=tenant_id, contact_id=contact_id)
    AuditService(session).record(
        AuditAction.CONTACT_DELETED,
        actor_type=principal.actor_type,
        actor=principal.user,
        tenant_id=tenant_id,
        entity_type="contact",
        entity_id=contact_id,
        meta={},
    )


@router.post(
    "/{tenant_id}/contacts/{contact_id}/block",
    response_model=ContactView,
    summary="Block or unblock a contact",
)
async def block_contact(
    contact_id: uuid.UUID,
    principal: RequireAdmin,
    session: SessionDep,
    blocked: bool = Query(default=True),
) -> ContactView:
    """Moves the contact between the two tabs and records the decision.

    It does **not** stop their calls being answered. Refusing a caller is a
    separate decision with its own consequences — a blocked customer who
    cannot get through does not know why — and the product does not make it
    on a business's behalf. The UI says as much where the action is offered.
    """
    tenant_id = _tenant_of(principal)
    service = ContactService(session)
    contact = await service.set_blocked(tenant_id=tenant_id, contact_id=contact_id, blocked=blocked)
    AuditService(session).record(
        AuditAction.CONTACT_BLOCKED if blocked else AuditAction.CONTACT_UNBLOCKED,
        actor_type=principal.actor_type,
        actor=principal.user,
        tenant_id=tenant_id,
        entity_type="contact",
        entity_id=contact.id,
        meta={"phone_e164": contact.phone_e164},
    )
    return _view(await service.get(tenant_id=tenant_id, contact_id=contact.id))
