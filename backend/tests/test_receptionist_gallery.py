"""The gallery: every receptionist one account has.

This is the only route in the portal that deliberately reads **across** tenants,
which makes its filtering the whole security story. Everything else here is
about telling an owner the truth on one screen:

* someone else's receptionist can never appear, and neither can one from an
  invitation that was never accepted — an invitation grants nothing until it is
  taken up, and listing it would put a stranger's business in this gallery;
* "live" is not "active". A business can be paid up with nothing answering yet,
  and the card has to say which;
* a deleted business still appears, because the account still owns its calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import BusinessProfile, Membership, Tenant
from app.models.enums import MembershipRole, TenantStatus
from tests.portal_support import live_owner, sign_up

URL = "/api/v1/me/receptionists"


async def _card(client: AsyncClient, owner: Any, tenant_id: uuid.UUID | None = None) -> Any:
    response = await client.get(URL, headers=owner.headers)
    assert response.status_code == 200, response.text
    cards = response.json()
    wanted = str(tenant_id or owner.tenant_id)
    found = [card for card in cards if card["tenant_id"] == wanted]
    assert len(found) == 1, cards
    return found[0]


async def _seed_calls(api_app: FastAPI, tenant_id: uuid.UUID, *, recent: int, old: int) -> None:
    from tests.factories import make_call

    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        now = datetime.now(UTC)
        for index in range(recent):
            session.add(make_call(tenant, started_at=now - timedelta(days=index % 20 + 1)))
        for index in range(old):
            session.add(make_call(tenant, started_at=now - timedelta(days=60 + index)))
        await session.commit()


# ---------------------------------------------------------------------------
# What a card says
# ---------------------------------------------------------------------------


async def test_the_gallery_describes_each_receptionist(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)

    card = await _card(api_client, owner)

    assert card["name"] == "Sunset Salon"
    assert card["status"] == "active"
    assert card["role"] == "owner"
    assert card["live"] is True, "provisioning finished, so something is answering"
    assert card["phone_e164"], "a live receptionist has a number"
    assert card["calls_recent"] == 0
    assert card["activity_days"] == 30


async def test_a_card_counts_only_recent_calls(api_client: AsyncClient, api_app: FastAPI) -> None:
    """ "12 calls in the last 30 days" has to mean that, or it means nothing."""
    owner = await live_owner(api_client, api_app)
    await _seed_calls(api_app, owner.tenant_id, recent=5, old=3)

    card = await _card(api_client, owner)

    assert card["calls_recent"] == 5


async def test_a_cards_face_follows_the_voice_it_speaks_with(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The id is stored; the name and the face are read from the catalogue.

    Derived rather than stored so that the gallery and the voice picker cannot
    disagree about who this receptionist is — the same voice is the same face
    in both places, because both ask the same function.
    """
    from app.services.avatars import avatar_for_voice

    owner = await live_owner(api_client, api_app)
    async with api_app.state.session_factory() as session:
        profile = (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == owner.tenant_id)
            )
        ).scalar_one()
        profile.voice_id = "cjVigY5qzO86Huf0OWal"  # Eric, male, middle aged
        profile.agent_name = "Hope"
        await session.commit()

    card = await _card(api_client, owner)

    assert card["voice"] == "Eric"
    assert card["agent_name"] == "Hope"
    assert card["avatar"] == avatar_for_voice(
        voice_id="cjVigY5qzO86Huf0OWal", gender="male", age="middle_aged"
    )


async def test_a_card_with_no_voice_of_its_own_claims_no_face(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The client draws one from the tenant id instead, which is stable."""
    owner = await live_owner(api_client, api_app)

    card = await _card(api_client, owner)

    assert card["avatar"] == ""


async def test_a_receptionist_with_no_voice_of_its_own_says_nothing_about_one(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The greeting style's default is resolved at publish time.

    Naming it here would mean guessing at that resolution a second time, and a
    gallery that disagreed with the Agent page about which voice is in use is
    worse than one that leaves the line out.
    """
    owner = await live_owner(api_client, api_app)

    card = await _card(api_client, owner)

    assert card["voice"] is None


# ---------------------------------------------------------------------------
# Whose receptionists these are
# ---------------------------------------------------------------------------


async def test_the_gallery_needs_a_session(api_client: AsyncClient) -> None:
    assert (await api_client.get(URL)).status_code == 401


async def test_another_accounts_receptionist_never_appears(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    stranger = await live_owner(api_client, api_app)

    response = await api_client.get(URL, headers=owner.headers)

    listed = {card["tenant_id"] for card in response.json()}
    assert str(owner.tenant_id) in listed
    assert str(stranger.tenant_id) not in listed


async def test_an_invitation_that_was_never_accepted_grants_nothing(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A pending invitation is not access, and the gallery is read by it.

    Without the ``accepted_at`` filter, inviting somebody would put your
    business in their gallery before they ever agreed to join it.
    """
    owner = await live_owner(api_client, api_app)
    invitee = await sign_up(api_client)
    async with api_app.state.session_factory() as session:
        user_id = (
            await session.execute(
                select(Membership.user_id).where(Membership.tenant_id == invitee.tenant_id)
            )
        ).scalar_one()
        session.add(
            Membership(
                user_id=user_id,
                tenant_id=owner.tenant_id,
                role=MembershipRole.MEMBER,
                accepted_at=None,
            )
        )
        await session.commit()

    response = await api_client.get(URL, headers=invitee.headers)

    listed = {card["tenant_id"] for card in response.json()}
    assert str(owner.tenant_id) not in listed
    assert str(invitee.tenant_id) in listed


async def test_a_deleted_business_is_still_in_the_gallery(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The account owns its calls, so hiding it would be losing it.

    The card reports the state; the page is what declines to offer anything
    that would act on one.
    """
    owner = await live_owner(api_client, api_app)
    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, owner.tenant_id)
        assert tenant is not None
        tenant.status = TenantStatus.CANCELLED
        await session.commit()

    card = await _card(api_client, owner)

    assert card["status"] == "cancelled"
