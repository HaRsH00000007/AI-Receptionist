"""Importing and exporting contacts as CSV.

Two different standards of behaviour, on purpose:

**Forgiving on the way in.** Spreadsheets name these columns a dozen ways, write
numbers with brackets and dashes, and Excel adds a byte-order mark. All of that
is accepted. What is *not* accepted is silence: every row that does not import
is counted and explained, with the row number a person sees in their file.

**Exact on the way out.** A note containing a comma, a quote or a newline has to
survive the round trip, and a value beginning with ``=`` has to arrive as text
rather than as something a spreadsheet will execute.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Contact
from app.models.enums import ContactStatus, MembershipRole
from tests.portal_support import add_member, live_owner

CONTACTS = "/api/v1/tenants/{tenant_id}/contacts"


async def _seed(
    api_app: FastAPI,
    tenant_id: uuid.UUID,
    *,
    phone: str,
    name: str | None = None,
    note: str | None = None,
    status: ContactStatus = ContactStatus.ACTIVE,
) -> None:
    async with api_app.state.session_factory() as session:
        session.add(
            Contact(
                tenant_id=tenant_id,
                phone_e164=phone,
                full_name=name,
                note=note,
                status=status,
            )
        )
        await session.commit()


async def _upload(client: AsyncClient, owner: Any, csv_text: str) -> dict[str, Any]:
    response = await client.post(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/import",
        files={"file": ("contacts.csv", csv_text.encode("utf-8"), "text/csv")},
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _listed(client: AsyncClient, owner: Any, **params: Any) -> dict[str, Any]:
    response = await client.get(
        CONTACTS.format(tenant_id=owner.tenant_id), params=params, headers=owner.headers
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


async def test_a_csv_of_contacts_is_imported(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)

    report = await _upload(
        api_client,
        owner,
        "Full name,Phone number,Note\n"
        "Dana Rivera,(805) 555-0142,Regular\n"
        "Sam Okafor,+1 212 555 1000,\n",
    )

    assert report["imported"] == 2
    assert report["invalid"] == 0
    body = await _listed(api_client, owner)
    assert {row["phone_e164"] for row in body["contacts"]} == {"+18055550142", "+12125551000"}
    assert all(row["source"] == "import" for row in body["contacts"])


async def test_import_accepts_the_headers_spreadsheets_actually_produce(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)

    report = await _upload(api_client, owner, "name,mobile,comments\nDana,8055550142,VIP\n")

    assert report["imported"] == 1
    body = await _listed(api_client, owner)
    assert body["contacts"][0]["note"] == "VIP"


async def test_import_survives_the_byte_order_mark_excel_adds(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)

    response = await api_client.post(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/import",
        files={
            "file": (
                "contacts.csv",
                "﻿Full name,Phone number\nDana,8055550142\n".encode(),
                "text/csv",
            )
        },
        headers=owner.headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1


async def test_import_reports_bad_rows_instead_of_dropping_them(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)

    report = await _upload(
        api_client,
        owner,
        "Full name,Phone number\n"
        "Good Person,8055550142\n"
        "No Number,\n"
        "Bad Number,12345\n"
        "Letters,not-a-phone\n",
    )

    assert report["imported"] == 1
    assert report["invalid"] == 3
    # The row numbers a person sees in their own spreadsheet, header included.
    assert [error["row"] for error in report["errors"]] == [3, 4, 5]
    assert all(error["reason"] for error in report["errors"])


async def test_import_detects_duplicates_in_the_file_and_already_on_file(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await _seed(api_app, owner.tenant_id, phone="+18055550142", name="Dana Rivera")

    report = await _upload(
        api_client,
        owner,
        "Full name,Phone number\n"
        "Dana Rivera,8055550142\n"
        "Someone,2125551000\n"
        "Someone Again,(212) 555-1000\n",
    )

    # One already on file with nothing to add; one repeated inside the file.
    assert report["imported"] == 1
    assert report["skipped_duplicate"] == 2
    async with api_app.state.session_factory() as session:
        contacts = (await session.execute(select(Contact))).scalars().all()
    assert len(contacts) == 2


async def test_import_fills_gaps_but_never_overwrites(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await _seed(api_app, owner.tenant_id, phone="+18055550142", name="Dana Rivera")

    report = await _upload(
        api_client, owner, "Full name,Phone number,Note\nWRONG NAME,8055550142,Prefers mornings\n"
    )

    assert report["updated"] == 1
    async with api_app.state.session_factory() as session:
        contact = (
            await session.execute(select(Contact).where(Contact.phone_e164 == "+18055550142"))
        ).scalar_one()
    assert contact.full_name == "Dana Rivera", "a name already on file is never replaced"
    assert contact.note == "Prefers mornings", "but an empty note is filled in"


async def test_a_file_with_no_phone_column_is_refused(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)

    response = await api_client.post(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/import",
        files={"file": ("contacts.csv", b"Full name,Email\nDana,dana@example.com\n", "text/csv")},
        headers=owner.headers,
    )

    assert response.status_code == 422
    assert "phone number column" in response.json()["error"]["message"]


async def test_importing_needs_admin(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    member = await add_member(api_app, owner.tenant_id, MembershipRole.MEMBER)

    response = await api_client.post(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/import",
        files={"file": ("contacts.csv", b"name,phone\nDana,8055550142\n", "text/csv")},
        headers=member,
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def test_export_round_trips_awkward_values(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    await _seed(
        api_app,
        owner.tenant_id,
        phone="+18055550142",
        name='Dana "Dee" Rivera',
        note="Prefers mornings, not evenings\nAsk about the dog",
    )

    response = await api_client.get(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/export", headers=owner.headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["Full name", "Phone number", "Note", "Status", "Date added"]
    assert rows[1][0] == 'Dana "Dee" Rivera'
    assert rows[1][1] == "+18055550142"
    assert rows[1][2] == "Prefers mornings, not evenings\nAsk about the dog"


async def test_export_neutralises_a_value_a_spreadsheet_would_execute(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await _seed(api_app, owner.tenant_id, phone="+18055550142", name="Dana", note="=HYPERLINK(1,2)")

    response = await api_client.get(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/export", headers=owner.headers
    )

    rows = list(csv.reader(io.StringIO(response.text)))
    # Quoted as text, so Excel shows the formula rather than running it.
    assert rows[1][2].startswith("'=")


async def test_export_matches_the_filter_on_screen(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await _seed(api_app, owner.tenant_id, phone="+18055550142", name="Active Person")
    await _seed(
        api_app,
        owner.tenant_id,
        phone="+18055550143",
        name="Blocked Person",
        status=ContactStatus.BLOCKED,
    )

    active = await api_client.get(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/export",
        params={"contact_status": "active"},
        headers=owner.headers,
    )
    everyone = await api_client.get(
        f"{CONTACTS.format(tenant_id=owner.tenant_id)}/export",
        params={"contact_status": "all"},
        headers=owner.headers,
    )

    assert "Active Person" in active.text
    assert "Blocked Person" not in active.text
    assert "Blocked Person" in everyone.text


async def test_a_concurrent_import_of_the_same_number_keeps_the_rest_of_the_file(
    api_client: AsyncClient, api_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two imports racing on one number must not discard the whole upload.

    The duplicate lookup reads before it writes, so it cannot see a row another
    import is inserting at that moment — the unique constraint is the real
    guard. Before each insert got its own savepoint, that constraint aborted the
    whole transaction and every valid row in the file went down with it.

    The race is simulated by making the lookup miss, which is exactly what a
    stale read looks like from inside the service.
    """
    from app.services.contacts import ContactService

    owner = await live_owner(api_client, api_app)
    await _seed(api_app, owner.tenant_id, phone="+18055550142", name="Already Here")

    async def stale_read(self: ContactService, tenant_id: object, phone: object) -> None:
        return None

    monkeypatch.setattr(ContactService, "_by_phone", stale_read)

    report = await _upload(
        api_client,
        owner,
        "Full name,Phone number\nDana Rivera,8055550142\nSam Okafor,2125551000\n",
    )

    # The colliding row is reported as a duplicate; the good row still lands.
    assert report["skipped_duplicate"] == 1
    assert report["imported"] == 1

    async with api_app.state.session_factory() as session:
        contacts = (await session.execute(select(Contact))).scalars().all()
    numbers = sorted(contact.phone_e164 for contact in contacts)
    assert numbers == ["+12125551000", "+18055550142"], "one new row, no duplicate"
    kept = next(contact for contact in contacts if contact.phone_e164 == "+18055550142")
    assert kept.full_name == "Already Here", "the existing record is untouched"
