"""Call tags: the labels a business sorts its calls by.

A tag is the first piece of configuration here that a *model* reads and that a
*filter* accepts, which is what the tests below are mostly about:

* another business's tag is a 404 to edit and matches nothing to filter by — the
  second half matters because a tag id is accepted as a query parameter, so an
  id that crossed a tenant boundary would cross it where calls are selected;
* the 25 limit counts *active* tags and holds under concurrent creates, which a
  count-then-insert cannot do on its own;
* deleting a tag takes its history with it and disabling one does not, because
  that difference is the whole reason both buttons exist.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models import CallTag, CallTagAssignment
from app.models.enums import MembershipRole
from app.services.call_tags import MAX_TAGS
from tests.portal_support import add_member, live_owner


def _tag(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Pricing",
        "description": "Caller asks about pricing, invoices, refunds or payment methods.",
        "color": "amber",
    }
    payload.update(overrides)
    return payload


def _url(owner: Any, tag_id: Any = None) -> str:
    base = f"/api/v1/tenants/{owner.tenant_id}/agent/tags"
    return base if tag_id is None else f"{base}/{tag_id}"


async def _add(client: AsyncClient, owner: Any, **overrides: Any) -> dict[str, Any]:
    response = await client.post(_url(owner), json=_tag(**overrides), headers=owner.headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _seed_call(api_app: FastAPI, tenant_id: uuid.UUID) -> uuid.UUID:
    from app.models import Tenant
    from tests.factories import make_call

    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        call = make_call(tenant)
        session.add(call)
        await session.commit()
        return call.id


async def _assign(api_app: FastAPI, call_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    async with api_app.state.session_factory() as session:
        session.add(CallTagAssignment(call_id=call_id, tag_id=tag_id))
        await session.commit()


async def _assignments(api_app: FastAPI) -> int:
    async with api_app.state.session_factory() as session:
        return (
            await session.execute(select(func.count()).select_from(CallTagAssignment))
        ).scalar_one()


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


async def test_a_new_receptionist_has_no_tags(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)

    response = await api_client.get(_url(owner), headers=owner.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tags"] == []
    assert body["max_tags"] == MAX_TAGS
    assert body["active"] == 0


async def test_the_tags_need_a_session(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    assert (await api_client.get(_url(owner))).status_code == 401


# ---------------------------------------------------------------------------
# Adding and editing
# ---------------------------------------------------------------------------


async def test_a_tag_is_stored_with_the_rule_for_applying_it(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The description is the mechanism, not documentation."""
    owner = await live_owner(api_client, api_app)

    body = await _add(api_client, owner)

    assert body["active"] == 1
    tag = body["tags"][0]
    assert tag["name"] == "Pricing"
    assert tag["description"].startswith("Caller asks about pricing")
    assert tag["color"] == "amber"
    assert tag["enabled"] is True


async def test_a_tag_with_no_rule_is_refused(api_client: AsyncClient, api_app: FastAPI) -> None:
    """A tag with an empty description is offered to the classifier with no rule
    for when it applies, and every call becomes a candidate for it."""
    owner = await live_owner(api_client, api_app)

    blank = await api_client.post(_url(owner), json=_tag(description="   "), headers=owner.headers)
    nameless = await api_client.post(_url(owner), json=_tag(name="  "), headers=owner.headers)

    assert blank.status_code == 422, blank.text
    assert nameless.status_code == 422, nameless.text


async def test_two_tags_cannot_share_a_name(api_client: AsyncClient, api_app: FastAPI) -> None:
    """Two labels with one name are two things the model cannot tell apart."""
    owner = await live_owner(api_client, api_app)
    await _add(api_client, owner)

    response = await api_client.post(_url(owner), json=_tag(), headers=owner.headers)

    assert response.status_code == 409, response.text


async def test_a_tag_can_be_renamed_and_recoloured(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The name lives in one place, so renaming it renames it on old calls too."""
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    tag_id = created["tags"][0]["id"]

    response = await api_client.patch(
        _url(owner, tag_id), json={"name": "Billing", "color": "red"}, headers=owner.headers
    )

    assert response.status_code == 200, response.text
    tag = response.json()["tags"][0]
    assert (tag["name"], tag["color"]) == ("Billing", "red")


# ---------------------------------------------------------------------------
# The limit
# ---------------------------------------------------------------------------


async def test_only_active_tags_count_against_the_limit(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Retiring a tag frees a slot without destroying the history it explains.

    The limit exists to keep the classifier's prompt readable, and a disabled
    tag is not in that prompt.
    """
    owner = await live_owner(api_client, api_app)
    for index in range(MAX_TAGS):
        await _add(api_client, owner, name=f"Tag {index}")

    refused = await api_client.post(_url(owner), json=_tag(name="One more"), headers=owner.headers)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["max_tags"] == MAX_TAGS

    # Switch one off, and there is room again.
    first = (await api_client.get(_url(owner), headers=owner.headers)).json()["tags"][0]["id"]
    await api_client.patch(_url(owner, first), json={"enabled": False}, headers=owner.headers)

    allowed = await api_client.post(_url(owner), json=_tag(name="One more"), headers=owner.headers)
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["active"] == MAX_TAGS


async def test_switching_a_tag_back_on_respects_the_limit(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Enabling competes for a slot exactly as creating does."""
    owner = await live_owner(api_client, api_app)
    spare = await _add(api_client, owner, name="Spare", enabled=False)
    spare_id = spare["tags"][0]["id"]
    for index in range(MAX_TAGS):
        await _add(api_client, owner, name=f"Tag {index}")

    response = await api_client.patch(
        _url(owner, spare_id), json={"enabled": True}, headers=owner.headers
    )

    assert response.status_code == 409, response.text


async def test_the_limit_holds_when_tags_are_created_at_once(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Counting and then inserting is a race; the tenant row is locked for it.

    Without the lock two requests can both count 24 and both insert, and the
    business ends up with 26 active tags and a prompt nobody meant to send.
    """
    owner = await live_owner(api_client, api_app)
    for index in range(MAX_TAGS - 1):
        await _add(api_client, owner, name=f"Tag {index}")

    # Four at once for the single remaining slot.
    results = await asyncio.gather(
        *(
            api_client.post(_url(owner), json=_tag(name=f"Race {index}"), headers=owner.headers)
            for index in range(4)
        )
    )

    created = [response for response in results if response.status_code == 201]
    assert len(created) == 1, [response.status_code for response in results]
    async with api_app.state.session_factory() as session:
        active = (
            await session.execute(
                select(func.count(CallTag.id)).where(
                    CallTag.tenant_id == owner.tenant_id, CallTag.enabled.is_(True)
                )
            )
        ).scalar_one()
    assert active == MAX_TAGS


# ---------------------------------------------------------------------------
# Deleting, and what it takes with it
# ---------------------------------------------------------------------------


async def test_deleting_a_tag_removes_it_from_the_calls_it_marked(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The cascade is the documented behaviour, and the reason "off" exists."""
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    tag_id = uuid.UUID(created["tags"][0]["id"])
    call_id = await _seed_call(api_app, owner.tenant_id)
    await _assign(api_app, call_id, tag_id)
    assert await _assignments(api_app) == 1

    response = await api_client.delete(_url(owner, tag_id), headers=owner.headers)

    assert response.status_code == 200, response.text
    assert response.json()["tags"] == []
    assert await _assignments(api_app) == 0


async def test_disabling_a_tag_keeps_the_calls_it_already_marked(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The difference between the two controls, asserted rather than described."""
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    tag_id = uuid.UUID(created["tags"][0]["id"])
    call_id = await _seed_call(api_app, owner.tenant_id)
    await _assign(api_app, call_id, tag_id)

    response = await api_client.patch(
        _url(owner, tag_id), json={"enabled": False}, headers=owner.headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["tags"][0]["enabled"] is False
    assert await _assignments(api_app) == 1, "the history survives"


async def test_deleting_a_call_takes_its_labels_with_it(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The other half of the referential integrity: an assignment cannot outlive
    either side of the pair it joins."""
    from app.models import Call

    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    tag_id = uuid.UUID(created["tags"][0]["id"])
    call_id = await _seed_call(api_app, owner.tenant_id)
    await _assign(api_app, call_id, tag_id)

    async with api_app.state.session_factory() as session:
        call = await session.get(Call, call_id)
        assert call is not None
        await session.delete(call)
        await session.commit()

    assert await _assignments(api_app) == 0


async def test_assigning_the_same_tag_twice_is_one_row(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """What makes a retried classification harmless: the pair is the key."""
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    tag_id = uuid.UUID(created["tags"][0]["id"])
    call_id = await _seed_call(api_app, owner.tenant_id)

    await _assign(api_app, call_id, tag_id)
    async with api_app.state.session_factory() as session:
        await session.merge(CallTagAssignment(call_id=call_id, tag_id=tag_id))
        await session.commit()

    assert await _assignments(api_app) == 1


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------


async def test_a_member_can_read_tags_but_not_change_them(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    tag_id = created["tags"][0]["id"]
    member = await add_member(api_app, owner.tenant_id, MembershipRole.MEMBER)

    assert (await api_client.get(_url(owner), headers=member)).status_code == 200
    assert (
        await api_client.post(_url(owner), json=_tag(name="X"), headers=member)
    ).status_code == 403
    assert (
        await api_client.patch(_url(owner, tag_id), json={"enabled": False}, headers=member)
    ).status_code == 403
    assert (await api_client.delete(_url(owner, tag_id), headers=member)).status_code == 403


async def test_an_admin_may_change_them(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    admin = await add_member(api_app, owner.tenant_id, MembershipRole.ADMIN)

    response = await api_client.post(_url(owner), json=_tag(), headers=admin)

    assert response.status_code == 201, response.text


async def test_another_businesss_tag_is_simply_not_found(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    stranger = await live_owner(api_client, api_app)
    created = await _add(api_client, stranger)
    tag_id = created["tags"][0]["id"]

    edited = await api_client.patch(
        _url(owner, tag_id), json={"enabled": False}, headers=owner.headers
    )
    removed = await api_client.delete(_url(owner, tag_id), headers=owner.headers)

    assert edited.status_code == 404, edited.text
    assert removed.status_code == 404, removed.text
    # And it is still theirs, untouched.
    theirs = await api_client.get(_url(stranger), headers=stranger.headers)
    assert theirs.json()["tags"][0]["enabled"] is True


async def test_two_businesses_may_use_the_same_tag_name(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The uniqueness is per tenant, not global."""
    owner = await live_owner(api_client, api_app)
    stranger = await live_owner(api_client, api_app)

    await _add(api_client, owner)
    response = await api_client.post(_url(stranger), json=_tag(), headers=stranger.headers)

    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# Filtering calls by a tag
# ---------------------------------------------------------------------------


async def test_calls_can_be_filtered_by_tag_server_side(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    tag_id = uuid.UUID(created["tags"][0]["id"])
    tagged = await _seed_call(api_app, owner.tenant_id)
    await _seed_call(api_app, owner.tenant_id)
    await _assign(api_app, tagged, tag_id)

    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/calls?tag={tag_id}", headers=owner.headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [call["id"] for call in body] == [str(tagged)]
    assert body[0]["tags"][0]["name"] == "Pricing"


async def test_filtering_by_another_businesss_tag_matches_nothing(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The filter is scoped to the tenant that owns the *tag* as well as the one
    that owns the calls.

    Without that, passing a stranger's tag id would narrow your own calls by
    somebody else's label — and the shape of the result would tell you something
    about their configuration.
    """
    owner = await live_owner(api_client, api_app)
    stranger = await live_owner(api_client, api_app)
    theirs = uuid.UUID((await _add(api_client, stranger))["tags"][0]["id"])
    mine = await _seed_call(api_app, owner.tenant_id)
    # Deliberately assigned, so the only thing keeping it out is the tenant scope.
    await _assign(api_app, mine, theirs)

    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/calls?tag={theirs}", headers=owner.headers
    )

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_an_unknown_tag_filter_is_empty_rather_than_an_error(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await _seed_call(api_app, owner.tenant_id)

    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/calls?tag={uuid.uuid4()}", headers=owner.headers
    )

    assert response.status_code == 200, response.text
    assert response.json() == []
