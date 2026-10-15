"""Contacts: the address book, and where it comes from.

The rules worth protecting, in order of how much damage breaking them does:

* **A call never fails because a contact could not be written.** The call record
  and the summary email are obligations; an address-book entry is a convenience.
* **A typed name is never overwritten by a claimed one.** The business corrects
  a caller's name in the dashboard; a later call must not undo that.
* **One number, one contact.** Enforced by a database constraint, so a
  redelivered webhook and two concurrent imports both collide instead of
  duplicating.
* **Tenant isolation**, asserted by trying another business's contact rather
  than by reading the query and trusting it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Call, Contact
from app.models.enums import CallStatus, ContactSource, ContactStatus, MembershipRole
from tests.portal_support import add_member, live_owner, other_tenant

CONTACTS = "/api/v1/tenants/{tenant_id}/contacts"


async def _seed_contact(
    api_app: FastAPI,
    tenant_id: uuid.UUID,
    *,
    phone: str = "+18055550142",
    name: str | None = "Dana Rivera",
    note: str | None = None,
    status: ContactStatus = ContactStatus.ACTIVE,
    source: ContactSource = ContactSource.MANUAL,
) -> uuid.UUID:
    async with api_app.state.session_factory() as session:
        contact = Contact(
            tenant_id=tenant_id,
            phone_e164=phone,
            full_name=name,
            note=note,
            status=status,
            source=source,
        )
        session.add(contact)
        await session.commit()
        return contact.id


async def _seed_call(
    api_app: FastAPI,
    tenant_id: uuid.UUID,
    *,
    from_e164: str = "+15551110000",
    transcript: str = "Hi, this is Jamie, I would like an appointment.",
    status: CallStatus = CallStatus.RECEIVED,
) -> uuid.UUID:
    """A call waiting to be processed.

    The name comes from the transcript, not from a field: the processor
    overwrites ``caller_name`` with whatever the summarizer reads, so seeding
    the column directly would test nothing. The fake summarizer returns a name
    only when the transcript contains one, which is what makes the
    "no name is recorded as no name" test meaningful.
    """
    async with api_app.state.session_factory() as session:
        call = Call(
            tenant_id=tenant_id,
            provider_call_id=f"conv-{uuid.uuid4().hex}",
            from_e164=from_e164,
            to_e164="+18055550100",
            started_at=datetime.now(UTC) - timedelta(minutes=5),
            duration_s=120,
            status=status,
            transcript_json={"text": transcript},
        )
        session.add(call)
        await session.commit()
        return call.id


async def _list(client: AsyncClient, owner: Any, **params: Any) -> dict[str, Any]:
    response = await client.get(
        CONTACTS.format(tenant_id=owner.tenant_id), params=params, headers=owner.headers
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ---------------------------------------------------------------------------
# Creating, editing, deleting
# ---------------------------------------------------------------------------


async def test_a_contact_can_be_added_by_hand(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)

    created = await api_client.post(
        CONTACTS.format(tenant_id=owner.tenant_id),
        json={"phone_number": "(805) 555-0142", "full_name": "Dana Rivera", "note": "Regular"},
        headers=owner.headers,
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["phone_e164"] == "+18055550142", "normalized on the way in"
    assert body["full_name"] == "Dana Rivera"
    assert body["status"] == "active"
    assert body["source"] == "manual"
    assert body["call_count"] == 0


async def test_an_invalid_phone_number_is_refused(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)

    response = await api_client.post(
        CONTACTS.format(tenant_id=owner.tenant_id),
        json={"phone_number": "12345", "full_name": "Nope"},
        headers=owner.headers,
    )

    assert response.status_code == 422
    async with api_app.state.session_factory() as session:
        assert (await session.execute(select(Contact))).scalars().all() == []


async def test_the_same_number_cannot_be_added_twice(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    payload = {"phone_number": "8055550142", "full_name": "Dana"}
    first = await api_client.post(
        CONTACTS.format(tenant_id=owner.tenant_id), json=payload, headers=owner.headers
    )
    assert first.status_code == 201

    again = await api_client.post(
        CONTACTS.format(tenant_id=owner.tenant_id),
        json={"phone_number": "+1 805 555 0142", "full_name": "Dana again"},
        headers=owner.headers,
    )

    assert again.status_code == 409
    assert again.json()["error"]["details"]["field"] == "phone_number"


async def test_a_contact_can_be_edited(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    contact_id = await _seed_contact(api_app, owner.tenant_id, name=None)

    response = await api_client.patch(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/{contact_id}",
        json={"full_name": "Dana Rivera", "note": "Prefers mornings"},
        headers=owner.headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["full_name"] == "Dana Rivera"
    assert response.json()["note"] == "Prefers mornings"


async def test_a_contact_can_be_deleted_without_losing_its_calls(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await _seed_call(api_app, owner.tenant_id, from_e164="+18055550142")
    contact_id = await _seed_contact(api_app, owner.tenant_id)

    deleted = await api_client.delete(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/{contact_id}", headers=owner.headers
    )

    assert deleted.status_code == 204
    async with api_app.state.session_factory() as session:
        assert (await session.execute(select(Contact))).scalars().all() == []
        calls = (await session.execute(select(Call))).scalars().all()
    assert len(calls) == 1, "deleting an address-book entry is not erasing history"


# ---------------------------------------------------------------------------
# Listing, searching, filtering
# ---------------------------------------------------------------------------


async def test_contacts_are_listed_with_their_call_history(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await _seed_contact(api_app, owner.tenant_id, phone="+18055550142")
    await _seed_call(api_app, owner.tenant_id, from_e164="+18055550142")
    await _seed_call(api_app, owner.tenant_id, from_e164="+18055550142")

    body = await _list(api_client, owner)

    assert body["total"] == 1
    assert body["contacts"][0]["call_count"] == 2, "read from the calls, not stored"
    assert body["contacts"][0]["last_call_at"]


async def test_active_and_blocked_are_separate_tabs_with_counts(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await _seed_contact(api_app, owner.tenant_id, phone="+18055550142", name="Active Person")
    blocked = await _seed_contact(
        api_app, owner.tenant_id, phone="+18055550143", name="Nuisance Caller"
    )

    blocking = await api_client.post(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/{blocked}/block",
        params={"blocked": True},
        headers=owner.headers,
    )
    assert blocking.status_code == 200
    assert blocking.json()["status"] == "blocked"
    assert blocking.json()["blocked_at"]

    active = await _list(api_client, owner, contact_status="active")
    blocked_tab = await _list(api_client, owner, contact_status="blocked")

    assert [row["full_name"] for row in active["contacts"]] == ["Active Person"]
    assert [row["full_name"] for row in blocked_tab["contacts"]] == ["Nuisance Caller"]
    # Both counts come back on either tab, so the tabs need no second request.
    assert active["active_count"] == 1
    assert active["blocked_count"] == 1


async def test_blocking_does_not_stop_the_receptionist_answering(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Blocking marks a record. It is not a silent call-rejection feature.

    If that changes, it should change deliberately, with the UI saying so —
    this test is here to make the current promise explicit.
    """
    owner = await live_owner(api_client, api_app)
    contact_id = await _seed_contact(api_app, owner.tenant_id, phone="+15551110000")
    await api_client.post(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/{contact_id}/block",
        params={"blocked": True},
        headers=owner.headers,
    )

    from app.models.enums import TenantStatus
    from app.telephony.routing import CallDisposition, RoutingInput, decide

    # Routing decides from the tenant and the agent; it never consults the
    # address book, which is exactly what "blocking marks, it does not drop"
    # means in practice.
    decision = decide(
        RoutingInput(
            dialled_e164="+18055550100",
            tenant_id=owner.tenant_id,
            tenant_status=TenantStatus.ACTIVE,
            agent_id="agent_1",
            business_name="Sunset Salon",
        )
    )
    assert decision.disposition is CallDisposition.CONNECT_AGENT


async def test_contacts_can_be_searched_by_name_or_number(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await _seed_contact(api_app, owner.tenant_id, phone="+18055550142", name="Dana Rivera")
    await _seed_contact(api_app, owner.tenant_id, phone="+12125551000", name="Sam Okafor")

    by_name = await _list(api_client, owner, search="dana")
    by_digits = await _list(api_client, owner, search="(212) 555")

    assert [row["full_name"] for row in by_name["contacts"]] == ["Dana Rivera"]
    assert [row["phone_e164"] for row in by_digits["contacts"]] == ["+12125551000"]


async def test_a_search_wildcard_is_not_treated_as_a_pattern(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A '%' typed in the box is a character, not "match everything"."""
    owner = await live_owner(api_client, api_app)
    await _seed_contact(api_app, owner.tenant_id, name="Dana Rivera")

    body = await _list(api_client, owner, search="%")

    assert body["contacts"] == []


async def test_contacts_are_paginated(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    for index in range(5):
        await _seed_contact(
            api_app, owner.tenant_id, phone=f"+1805555010{index}", name=f"Person {index}"
        )

    first = await _list(api_client, owner, limit=2, offset=0)
    second = await _list(api_client, owner, limit=2, offset=2)

    assert first["total"] == 5
    assert len(first["contacts"]) == 2
    assert len(second["contacts"]) == 2
    assert {row["id"] for row in first["contacts"]}.isdisjoint(
        {row["id"] for row in second["contacts"]}
    )


# ---------------------------------------------------------------------------
# Authorization and isolation
# ---------------------------------------------------------------------------


async def test_another_business_cannot_see_or_touch_these_contacts(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    contact_id = await _seed_contact(api_app, owner.tenant_id)
    _, stranger = await other_tenant(api_app)

    listed = await api_client.get(CONTACTS.format(tenant_id=owner.tenant_id), headers=stranger)
    fetched = await api_client.get(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/{contact_id}", headers=stranger
    )

    # A business you cannot see is a 404, never a 403.
    assert listed.status_code == 404
    assert fetched.status_code == 404


async def test_a_member_can_read_but_not_change_contacts(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    contact_id = await _seed_contact(api_app, owner.tenant_id)
    member = await add_member(api_app, owner.tenant_id, MembershipRole.MEMBER)

    readable = await api_client.get(CONTACTS.format(tenant_id=owner.tenant_id), headers=member)
    writable = await api_client.patch(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/{contact_id}",
        json={"full_name": "Changed"},
        headers=member,
    )

    assert readable.status_code == 200
    assert writable.status_code == 403


async def test_contacts_need_a_session(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    assert (await api_client.get(CONTACTS.format(tenant_id=owner.tenant_id))).status_code == 401


# ---------------------------------------------------------------------------
# Captured from finished calls
# ---------------------------------------------------------------------------


async def _process_call(api_app: FastAPI, call_id: uuid.UUID) -> None:
    from app.services.call_processor import CallProcessor

    processor = CallProcessor(api_app.state.settings, api_app.state.providers)
    async with api_app.state.session_factory() as session:
        await processor.process(session, call_id)


async def test_a_finished_call_becomes_a_contact(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    call_id = await _seed_call(api_app, owner.tenant_id, from_e164="+15551110000")

    await _process_call(api_app, call_id)

    body = await _list(api_client, owner)
    captured = [row for row in body["contacts"] if row["phone_e164"] == "+15551110000"]
    assert captured, body
    assert captured[0]["source"] == "call"
    assert captured[0]["call_count"] == 1
    # The name the caller gave on the call, not one we invented.
    assert captured[0]["full_name"] == "Jamie Rivera"


async def test_a_repeated_call_does_not_create_a_second_contact(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Redelivered webhooks and retries must not duplicate the address book."""
    owner = await live_owner(api_client, api_app)
    first = await _seed_call(api_app, owner.tenant_id, from_e164="+15551110000")
    second = await _seed_call(api_app, owner.tenant_id, from_e164="+15551110000")

    await _process_call(api_app, first)
    await _process_call(api_app, second)
    await _process_call(api_app, first)  # a redelivery of the first

    async with api_app.state.session_factory() as session:
        contacts = (
            (await session.execute(select(Contact).where(Contact.phone_e164 == "+15551110000")))
            .scalars()
            .all()
        )
    assert len(contacts) == 1


async def test_a_caller_with_no_name_is_still_captured(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """No name is recorded as no name. Nothing is invented."""
    owner = await live_owner(api_client, api_app)
    call_id = await _seed_call(
        api_app,
        owner.tenant_id,
        from_e164="+15551112222",
        transcript="Hello, I need to book something for next week.",
    )

    await _process_call(api_app, call_id)

    async with api_app.state.session_factory() as session:
        contact = (
            await session.execute(select(Contact).where(Contact.phone_e164 == "+15551112222"))
        ).scalar_one()
    assert contact.full_name is None


async def test_a_typed_name_is_never_overwritten_by_a_claimed_one(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The business corrected the name; a later call must not undo that."""
    owner = await live_owner(api_client, api_app)
    await _seed_contact(
        api_app,
        owner.tenant_id,
        phone="+15551110000",
        name="Christopher Patel",
        note="VIP",
        source=ContactSource.MANUAL,
    )
    call_id = await _seed_call(api_app, owner.tenant_id, from_e164="+15551110000")

    await _process_call(api_app, call_id)

    async with api_app.state.session_factory() as session:
        contact = (
            await session.execute(select(Contact).where(Contact.phone_e164 == "+15551110000"))
        ).scalar_one()
    assert contact.full_name == "Christopher Patel"
    assert contact.note == "VIP"


async def test_a_call_still_succeeds_when_the_contact_cannot_be_written(
    api_client: AsyncClient, api_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the savepoint: the call and its email survive."""
    from app.models import Notification
    from app.services.contacts import ContactService

    owner = await live_owner(api_client, api_app)
    call_id = await _seed_call(api_app, owner.tenant_id, from_e164="+15551113333")

    async def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("contacts table is on fire")

    monkeypatch.setattr(ContactService, "capture_from_call", explode)

    await _process_call(api_app, call_id)

    async with api_app.state.session_factory() as session:
        call = await session.get(Call, call_id)
        notifications = (
            (await session.execute(select(Notification).where(Notification.call_id == call_id)))
            .scalars()
            .all()
        )
        contacts = (
            (await session.execute(select(Contact).where(Contact.phone_e164 == "+15551113333")))
            .scalars()
            .all()
        )
    assert call is not None and call.status is CallStatus.NOTIFIED, "the call still completed"
    assert notifications, "the summary email was still queued"
    assert contacts == [], "and no contact was written"


async def test_a_call_with_no_caller_id_never_becomes_a_contact(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A withheld number is nothing to put in an address book.

    Carried over from the derived-contacts test that this feature replaced.
    """
    owner = await live_owner(api_client, api_app)
    async with api_app.state.session_factory() as session:
        call = Call(
            tenant_id=owner.tenant_id,
            provider_call_id=f"conv-{uuid.uuid4().hex}",
            from_e164=None,
            to_e164="+18055550100",
            started_at=datetime.now(UTC) - timedelta(minutes=2),
            duration_s=30,
            status=CallStatus.RECEIVED,
            transcript_json={"text": "Hello, I would like an appointment."},
        )
        session.add(call)
        await session.commit()
        call_id = call.id

    await _process_call(api_app, call_id)

    async with api_app.state.session_factory() as session:
        contacts = (await session.execute(select(Contact))).scalars().all()
    assert contacts == []
