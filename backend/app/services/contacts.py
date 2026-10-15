"""Managing the people a business keeps on file.

Three jobs, and the interesting rules are in the third:

* **CRUD and listing** — search, status filter, pagination, with call counts
  read from the call history rather than stored, so the two cannot drift.
* **CSV** — import and export, which has to be forgiving of what spreadsheets
  produce and unforgiving about what it writes back.
* **Capture from a finished call** — the part that must never, under any
  circumstances, break a call.

Two decisions worth knowing:

**A typed name outranks a claimed one.** A caller says their name on the phone;
the business types it in the dashboard. If a later call disagrees with what a
person entered, the person wins — automatic capture only fills a name that is
missing, and never touches a note.

**Blocking marks, it does not drop.** Blocking a contact filters them out of the
active list and records the decision. It does not hang up on them: silently
refusing a caller is a thing a business should choose deliberately, and this is
not that choice.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.core.logging import get_logger
from app.models import Call, Contact
from app.models.enums import ContactSource, ContactStatus
from app.services.normalization import normalize_phone

logger = get_logger(__name__)

MAX_NAME = 200
MAX_NOTE = 2000
#: A spreadsheet of contacts, not a database dump. Large enough for a real
#: address book, small enough that one upload cannot exhaust memory.
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 5_000

#: Accepted header spellings, because every tool exports them differently.
NAME_HEADERS = ("full name", "name", "full_name", "contact name", "customer name")
PHONE_HEADERS = ("phone number", "phone", "phone_e164", "mobile", "number", "telephone")
NOTE_HEADERS = ("note", "notes", "comment", "comments")

EXPORT_HEADERS = ("Full name", "Phone number", "Note", "Status", "Date added")


@dataclass(slots=True)
class ContactRecord:
    """A contact plus the call history behind it."""

    contact: Contact
    call_count: int = 0
    first_call_at: datetime | None = None
    last_call_at: datetime | None = None
    last_summary: str | None = None


@dataclass(slots=True)
class ContactPage:
    """One page of contacts, with the counts both tabs need."""

    records: list[ContactRecord]
    total: int
    active_count: int
    blocked_count: int


@dataclass(slots=True)
class ImportReport:
    """What an import did, row by row, so nothing is silently discarded."""

    imported: int = 0
    updated: int = 0
    skipped_duplicate: int = 0
    invalid: int = 0
    #: ``(row number, reason)`` for every row that did not import. Row numbers
    #: are the ones a person sees in their spreadsheet, header included.
    errors: list[tuple[int, str]] = field(default_factory=list)

    def note(self, row: int, reason: str) -> None:
        self.invalid += 1
        if len(self.errors) < 50:
            self.errors.append((row, reason))


class ContactService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- reading ---------------------------------------------------------

    async def list(
        self,
        *,
        tenant_id: uuid.UUID,
        status: ContactStatus | None = None,
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> ContactPage:
        """A page of contacts, newest first, with call history attached.

        Every query here is filtered by ``tenant_id`` in its own WHERE clause
        rather than by a caller remembering to — one tenant's contacts are
        never reachable from another's session.
        """
        base = select(Contact).where(Contact.tenant_id == tenant_id)
        base = _searched(base, search)

        active_count, blocked_count = await self._counts(tenant_id, search)
        if status is ContactStatus.ACTIVE:
            total = active_count
        elif status is ContactStatus.BLOCKED:
            total = blocked_count
        else:
            total = active_count + blocked_count
        if status is not None:
            base = base.where(Contact.status == status)

        rows = (
            (
                await self.session.execute(
                    base.order_by(Contact.created_at.desc(), Contact.phone_e164)
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return ContactPage(
            records=await self._with_calls(tenant_id, list(rows)),
            total=total,
            active_count=active_count,
            blocked_count=blocked_count,
        )

    async def get(self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> ContactRecord:
        contact = await self._require(tenant_id, contact_id)
        records = await self._with_calls(tenant_id, [contact])
        return records[0]

    async def _counts(self, tenant_id: uuid.UUID, search: str) -> tuple[int, int]:
        counted = _searched(
            select(Contact.status, func.count(Contact.id)).where(Contact.tenant_id == tenant_id),
            search,
        )
        tally = dict(
            (await self.session.execute(counted.group_by(Contact.status))).all()  # type: ignore[arg-type]
        )
        return int(tally.get(ContactStatus.ACTIVE, 0)), int(tally.get(ContactStatus.BLOCKED, 0))

    async def _with_calls(
        self, tenant_id: uuid.UUID, contacts: list[Contact]
    ) -> list[ContactRecord]:
        """Attach call history. One extra query for the whole page, not per row."""
        if not contacts:
            return []
        numbers = [contact.phone_e164 for contact in contacts]
        started = func.coalesce(Call.started_at, Call.created_at)
        rows = (
            await self.session.execute(
                select(
                    Call.from_e164,
                    func.count(Call.id),
                    func.min(started),
                    func.max(started),
                )
                .where(Call.tenant_id == tenant_id, Call.from_e164.in_(numbers))
                .group_by(Call.from_e164)
            )
        ).all()
        stats = {row[0]: row for row in rows}
        return [
            ContactRecord(
                contact=contact,
                call_count=int(stats[contact.phone_e164][1]) if contact.phone_e164 in stats else 0,
                first_call_at=stats[contact.phone_e164][2] if contact.phone_e164 in stats else None,
                last_call_at=stats[contact.phone_e164][3] if contact.phone_e164 in stats else None,
            )
            for contact in contacts
        ]

    # ---- writing ---------------------------------------------------------

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        phone: str,
        full_name: str | None,
        note: str | None,
        source: ContactSource = ContactSource.MANUAL,
    ) -> Contact:
        """Add a contact, or refuse because the number is already on file."""
        contact = Contact(
            tenant_id=tenant_id,
            phone_e164=normalize_phone(phone),
            full_name=_clean(full_name, MAX_NAME),
            note=_clean(note, MAX_NOTE),
            status=ContactStatus.ACTIVE,
            source=source,
        )
        self.session.add(contact)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                "a contact with that phone number already exists",
                details={"field": "phone_number"},
            ) from exc
        return contact

    async def update(
        self,
        *,
        tenant_id: uuid.UUID,
        contact_id: uuid.UUID,
        phone: str | None = None,
        full_name: str | None = None,
        note: str | None = None,
    ) -> Contact:
        contact = await self._require(tenant_id, contact_id)
        if phone is not None:
            contact.phone_e164 = normalize_phone(phone)
        if full_name is not None:
            contact.full_name = _clean(full_name, MAX_NAME)
        if note is not None:
            contact.note = _clean(note, MAX_NOTE)
        # Edited by a person, so it stops being an automatic record and its
        # name is no longer something a later call may overwrite.
        contact.source = ContactSource.MANUAL
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                "another contact already has that phone number",
                details={"field": "phone_number"},
            ) from exc
        return contact

    async def delete(self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> None:
        """Remove the contact. Call history is untouched and stays visible."""
        contact = await self._require(tenant_id, contact_id)
        await self.session.delete(contact)
        await self.session.flush()

    async def set_blocked(
        self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID, blocked: bool
    ) -> Contact:
        contact = await self._require(tenant_id, contact_id)
        contact.status = ContactStatus.BLOCKED if blocked else ContactStatus.ACTIVE
        contact.blocked_at = datetime.now(UTC) if blocked else None
        await self.session.flush()
        return contact

    async def _require(self, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> Contact:
        contact = (
            await self.session.execute(
                select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if contact is None:
            # Scoped in the query: another tenant's contact id is
            # indistinguishable from one that does not exist.
            raise NotFoundError("contact not found")
        return contact

    async def _by_phone(self, tenant_id: uuid.UUID, phone: str) -> Contact | None:
        """The contact on this number, if there is one.

        Shared by the capture and import paths so they agree on what "already
        on file" means. It reads before it writes, so a row another request is
        inserting right now is invisible to it — which is why both callers also
        handle the unique constraint firing underneath them.
        """
        return (
            await self.session.execute(
                select(Contact).where(Contact.tenant_id == tenant_id, Contact.phone_e164 == phone)
            )
        ).scalar_one_or_none()

    # ---- capture from a finished call ------------------------------------

    async def capture_from_call(self, *, tenant_id: uuid.UUID, call: Call) -> Contact | None:
        """Record the caller as a contact, if we learned anything worth keeping.

        Safe to run again for the same call: the number is the identity, so a
        redelivered webhook updates the row it already made rather than adding
        another. Returns ``None`` when there is nothing to record — a withheld
        number, or an outbound call.

        Never raises for an ordinary conflict. The caller treats any failure
        here as non-fatal, because a contact is a convenience and a call
        record is not.
        """
        number = (call.from_e164 or "").strip()
        if not number:
            return None

        existing = await self._by_phone(tenant_id, number)

        claimed = _clean(call.caller_name, MAX_NAME)
        if existing is not None:
            # Only ever fills a gap. A name or note a person entered is never
            # overwritten by what a caller said on the phone.
            if not existing.full_name and claimed and existing.source is not ContactSource.MANUAL:
                existing.full_name = claimed
                await self.session.flush()
            return existing

        try:
            # A savepoint, not the whole transaction. This runs inside call
            # processing, where a queued summary email is already staged and
            # uncommitted — a plain rollback here would throw that away and the
            # business would lose the notification for a call that happened.
            async with self.session.begin_nested():
                contact = Contact(
                    tenant_id=tenant_id,
                    phone_e164=number,
                    full_name=claimed,
                    status=ContactStatus.ACTIVE,
                    source=ContactSource.CALL,
                )
                self.session.add(contact)
        except IntegrityError:
            # Two workers finished two calls from the same number at once. The
            # constraint did its job; read back the winner.
            return await self._by_phone(tenant_id, number)
        return contact

    # ---- CSV -------------------------------------------------------------

    async def import_csv(self, *, tenant_id: uuid.UUID, content: bytes) -> ImportReport:
        """Load a spreadsheet of contacts, reporting every row it could not.

        Forgiving about input: any common spelling of the headers, numbers with
        brackets and dashes, a UTF-8 byte-order mark from Excel. Strict about
        outcome: an invalid row is counted and explained, never dropped
        quietly.
        """
        if len(content) > MAX_CSV_BYTES:
            raise InvalidInputError(
                "that file is too large; split it into smaller files",
                details={"field": "file", "max_bytes": MAX_CSV_BYTES},
            )
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidInputError(
                "that file is not valid UTF-8 text; export it as CSV and try again",
                details={"field": "file"},
            ) from exc

        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise InvalidInputError("that file is empty", details={"field": "file"})

        columns = {(name or "").strip().lower(): name for name in reader.fieldnames}
        phone_column = _match(columns, PHONE_HEADERS)
        if phone_column is None:
            raise InvalidInputError(
                "the file needs a phone number column",
                details={"field": "file", "found": list(columns)},
            )
        name_column = _match(columns, NAME_HEADERS)
        note_column = _match(columns, NOTE_HEADERS)

        report = ImportReport()
        seen: set[str] = set()
        for index, row in enumerate(reader, start=2):  # row 1 is the header
            if index - 1 > MAX_CSV_ROWS:
                report.note(index, f"stopped after {MAX_CSV_ROWS} rows")
                break
            raw_phone = (row.get(phone_column) or "").strip()
            if not raw_phone:
                report.note(index, "no phone number")
                continue
            try:
                phone = normalize_phone(raw_phone)
            except InvalidInputError as exc:
                report.note(index, str(exc))
                continue
            if phone in seen:
                # Duplicated inside the file itself.
                report.skipped_duplicate += 1
                continue
            seen.add(phone)

            name = _clean(row.get(name_column) if name_column else None, MAX_NAME)
            note = _clean(row.get(note_column) if note_column else None, MAX_NOTE)

            existing = await self._by_phone(tenant_id, phone)
            if existing is not None:
                # Already on file: fill gaps, never overwrite what is there.
                changed = False
                if name and not existing.full_name:
                    existing.full_name = name
                    changed = True
                if note and not existing.note:
                    existing.note = note
                    changed = True
                if changed:
                    report.updated += 1
                else:
                    report.skipped_duplicate += 1
                continue

            try:
                # One savepoint per row. The lookup above cannot see a row that
                # another import is inserting right now, so the constraint is
                # the real duplicate guard — and without a savepoint its error
                # would abort the whole transaction and lose every valid row in
                # the file along with it.
                async with self.session.begin_nested():
                    self.session.add(
                        Contact(
                            tenant_id=tenant_id,
                            phone_e164=phone,
                            full_name=name,
                            note=note,
                            status=ContactStatus.ACTIVE,
                            source=ContactSource.IMPORT,
                        )
                    )
            except IntegrityError:
                # Someone else got there first. Not an error worth showing: the
                # contact exists, which is what the row asked for.
                report.skipped_duplicate += 1
                continue
            report.imported += 1

        await self.session.flush()
        logger.info(
            "contacts imported",
            extra={
                "tenant_id": str(tenant_id),
                "imported": report.imported,
                "updated": report.updated,
                "skipped": report.skipped_duplicate,
                "invalid": report.invalid,
            },
        )
        return report

    async def export_csv(
        self, *, tenant_id: uuid.UUID, status: ContactStatus | None = None, search: str = ""
    ) -> str:
        """Every contact matching the current filter, as CSV text.

        Written with :mod:`csv` rather than by joining strings, so a note
        containing a comma, a quote or a newline survives the round trip. Each
        field is also prefixed when it could be read as a formula by a
        spreadsheet — a note beginning ``=`` is text, not something to execute.
        """
        base = _searched(select(Contact).where(Contact.tenant_id == tenant_id), search)
        if status is not None:
            base = base.where(Contact.status == status)
        rows = (
            (await self.session.execute(base.order_by(Contact.created_at.desc()))).scalars().all()
        )

        buffer = io.StringIO()
        writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        writer.writerow(EXPORT_HEADERS)
        for contact in rows:
            writer.writerow(
                [
                    _safe(contact.full_name or ""),
                    contact.phone_e164,
                    _safe(contact.note or ""),
                    contact.status.value,
                    contact.created_at.date().isoformat() if contact.created_at else "",
                ]
            )
        return buffer.getvalue()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIGITS = re.compile(r"\D")


def _searched(statement: Select[Any], search: str) -> Select[Any]:
    """Filter by name or phone number.

    The number is matched on digits alone, so a search for "(805) 555" finds
    ``+18055550142``. Both sides are escaped against the LIKE wildcards.
    """
    term = search.strip()
    if not term:
        return statement
    text = f"%{_escape(term.lower())}%"
    clauses = [func.lower(Contact.full_name).like(text, escape="\\")]
    digits = _DIGITS.sub("", term)
    if digits:
        clauses.append(Contact.phone_e164.like(f"%{_escape(digits)}%", escape="\\"))
    else:
        clauses.append(Contact.phone_e164.like(text, escape="\\"))
    return statement.where(or_(*clauses))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())[:limit]
    return text or None


def _match(columns: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return columns[candidate]
    return None


def _safe(value: str) -> str:
    """Neutralise a value a spreadsheet would treat as a formula."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@") else value


__all__ = [
    "EXPORT_HEADERS",
    "ContactPage",
    "ContactRecord",
    "ContactService",
    "ImportReport",
]
