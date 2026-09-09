"""Cross-tenant access must be impossible, not merely absent.

Every test here builds **two** tenants with real rows and then actively tries to
reach one from the other. That framing matters: a test that only checks "tenant
A sees its own three calls" passes just as happily against a repository with no
tenant filter at all, because tenant B's rows were never created. The bug this
file exists to catch — a query missing its ``tenant_id`` predicate — is only
visible when there is someone else's data to leak.

The repository is exercised directly rather than through the API because this is
where the guarantee is implemented. An API-level test proves one endpoint is
scoped; this proves the mechanism every endpoint is built on.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Protocol

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import Repository, TenantScopedRepository
from app.models import (
    AgentConfig,
    Call,
    Notification,
    PhoneNumber,
    Subscription,
    Tenant,
    UsageEvent,
)
from tests.factories import (
    make_agent_config,
    make_call,
    make_notification,
    make_phone_number,
    make_subscription,
    make_tenant,
    make_usage_event,
)


class CallRepository(TenantScopedRepository[Call]):
    model = Call


class AgentConfigRepository(TenantScopedRepository[AgentConfig]):
    model = AgentConfig


class PhoneNumberRepository(TenantScopedRepository[PhoneNumber]):
    model = PhoneNumber


class UsageEventRepository(TenantScopedRepository[UsageEvent]):
    model = UsageEvent


class NotificationRepository(TenantScopedRepository[Notification]):
    model = Notification


class SubscriptionRepository(TenantScopedRepository[Subscription]):
    model = Subscription


class UnscopedCallRepository(Repository[Call]):
    """The unscoped counterpart, used only to prove the other tenant's row exists.

    If a cross-tenant read returns nothing, that could mean the filter worked or
    that the row was never written. This repository distinguishes the two, so a
    passing test cannot be passing for the wrong reason.
    """

    model = Call


class TenantOwnedRow(Protocol):
    """The two columns every tenant-owned row has.

    Structural rather than a base class, because the models already inherit
    their id and tenant_id from different places; this states what the
    parametrized test actually needs without widening to ``Base``, which has
    neither.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID


async def _two_tenants(db_session: AsyncSession) -> tuple[Tenant, Tenant]:
    ours, theirs = make_tenant(name="Ours"), make_tenant(name="Theirs")
    db_session.add_all([ours, theirs])
    await db_session.flush()
    return ours, theirs


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def test_get_refuses_another_tenants_row(db_session: AsyncSession) -> None:
    ours, theirs = await _two_tenants(db_session)
    their_call = make_call(theirs)
    db_session.add(their_call)
    await db_session.flush()

    scoped = CallRepository(db_session, ours.id)
    assert await scoped.get(their_call.id) is None

    # The row genuinely exists — the None above is the filter, not an empty
    # database.
    assert await UnscopedCallRepository(db_session).get(their_call.id) is not None


async def test_get_returns_a_404_shape_rather_than_a_403(db_session: AsyncSession) -> None:
    """A cross-tenant probe must not confirm that an id exists.

    Raising "forbidden" for someone else's row and "not found" for a nonexistent
    one turns the API into an oracle for enumerating other tenants' ids. Both
    cases must look identical.
    """
    ours, theirs = await _two_tenants(db_session)
    their_call = make_call(theirs)
    db_session.add(their_call)
    await db_session.flush()

    scoped = CallRepository(db_session, ours.id)
    assert await scoped.get(their_call.id) is None
    assert await scoped.get(uuid.uuid4()) is None


async def test_list_by_never_includes_another_tenants_rows(db_session: AsyncSession) -> None:
    ours, theirs = await _two_tenants(db_session)
    mine = [make_call(ours) for _ in range(2)]
    others = [make_call(theirs) for _ in range(3)]
    db_session.add_all([*mine, *others])
    await db_session.flush()

    found = await CallRepository(db_session, ours.id).list_by()
    assert {call.id for call in found} == {call.id for call in mine}


async def test_count_counts_only_this_tenant(db_session: AsyncSession) -> None:
    ours, theirs = await _two_tenants(db_session)
    db_session.add_all([make_call(ours), make_call(theirs), make_call(theirs)])
    await db_session.flush()

    assert await CallRepository(db_session, ours.id).count() == 1
    assert await CallRepository(db_session, theirs.id).count() == 2


async def test_find_one_by_cannot_reach_across_tenants(db_session: AsyncSession) -> None:
    """Even an exact match on a unique column stays inside the tenant."""
    ours, theirs = await _two_tenants(db_session)
    their_call = make_call(theirs, provider_call_id="conv_theirs_1")
    db_session.add(their_call)
    await db_session.flush()

    scoped = CallRepository(db_session, ours.id)
    assert await scoped.find_one_by(provider_call_id="conv_theirs_1") is None
    assert (
        await CallRepository(db_session, theirs.id).find_one_by(provider_call_id="conv_theirs_1")
        is not None
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def test_add_stamps_the_repositorys_tenant(db_session: AsyncSession) -> None:
    """A caller that omits tenant_id gets the right one rather than a null."""
    ours, _ = await _two_tenants(db_session)
    call = make_call(ours)
    call.tenant_id = None  # type: ignore[assignment]

    CallRepository(db_session, ours.id).add(call)
    assert call.tenant_id == ours.id


async def test_add_refuses_a_row_belonging_to_another_tenant(db_session: AsyncSession) -> None:
    """A mismatch is a bug, so it is refused rather than silently corrected.

    Quietly rewriting the tenant would turn a caller's mistake into a
    successful write against the wrong customer — the exact outcome this whole
    class exists to prevent.
    """
    ours, theirs = await _two_tenants(db_session)
    their_call = make_call(theirs)

    with pytest.raises(PermissionError, match="refusing to write"):
        CallRepository(db_session, ours.id).add(their_call)


async def test_delete_refuses_another_tenants_row(db_session: AsyncSession) -> None:
    ours, theirs = await _two_tenants(db_session)
    their_call = make_call(theirs)
    db_session.add(their_call)
    await db_session.flush()

    with pytest.raises(PermissionError, match="refusing to delete"):
        await CallRepository(db_session, ours.id).delete(their_call)


# ---------------------------------------------------------------------------
# The guarantee holds for every tenant-owned model, not just calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("repository_class", "factory"),
    [
        (CallRepository, make_call),
        (AgentConfigRepository, make_agent_config),
        (PhoneNumberRepository, make_phone_number),
        (UsageEventRepository, make_usage_event),
        (NotificationRepository, make_notification),
        (SubscriptionRepository, make_subscription),
    ],
    ids=["calls", "agent_configs", "phone_numbers", "usage_events", "notifications", "subs"],
)
async def test_every_tenant_owned_model_is_isolated(
    db_session: AsyncSession,
    repository_class: type[TenantScopedRepository[Any]],
    factory: Callable[[Tenant], TenantOwnedRow],
) -> None:
    """Parametrized so a new tenant-owned model is one line, not a new test.

    Isolation that holds for calls and quietly fails for usage events is not
    isolation; the property has to be checked per model.
    """
    ours, theirs = await _two_tenants(db_session)
    their_row = factory(theirs)
    db_session.add(their_row)
    await db_session.flush()

    scoped = repository_class(db_session, ours.id)
    row_id = their_row.id
    assert await scoped.get(row_id) is None
    assert await scoped.list_by() == []
    assert await scoped.count() == 0


# ---------------------------------------------------------------------------
# The guard on the class itself
# ---------------------------------------------------------------------------


def test_a_tenantless_model_cannot_be_tenant_scoped() -> None:
    """Caught at import time, not on the first query in production."""
    from app.models import User

    with pytest.raises(TypeError, match="has no tenant_id column"):

        class BadRepository(TenantScopedRepository[User]):
            model = User


def test_the_scoped_repository_exposes_no_unscoped_read() -> None:
    """No escape hatch.

    If an ``all()`` or ``unscoped()`` helper existed here, it would eventually
    be used by someone in a hurry, and the guarantee would become a convention.
    Cross-tenant work uses the plain Repository, which is greppable.
    """
    unscoped_names = {"all", "unscoped", "without_tenant", "get_any", "list_all"}
    assert unscoped_names.isdisjoint(dir(TenantScopedRepository))
