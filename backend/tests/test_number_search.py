"""Choosing a number: search first, then buy the one that was chosen.

Search and purchase are separate here the way they are at the vendor, so these
tests pin the two rules that make the customer-facing behaviour honest:

* alternatives appear **only** when the requested area code is genuinely empty;
* they are found **geographically** — 916 offers other California codes, never
  917 because it happens to be one digit away.

The purchase tests then cover the other half: a chosen number is bought as
chosen, and one that someone else took in the meantime falls back to the
existing ladder instead of failing the run.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.models import PhoneNumber, Tenant
from app.models.enums import PhoneNumberStatus, TenantStatus
from app.providers.fakes.telephony import FakeTwilioProvider
from app.providers.models import AvailableNumber
from app.schemas.signup import SignupRequest
from app.services.normalization import area_code_of
from app.services.number_search import (
    STRATEGY_EXACT,
    STRATEGY_NONE,
    STRATEGY_SAME_STATE,
    NumberSearchService,
    _spread,
)

#: New York, and stocked in the fake.
STOCKED = "212"
#: Sacramento: a real area code with no inventory, in a state that has some.
EMPTY_WITH_NEIGHBOURS = "916"
#: Alaska has exactly one area code, and the fake stocks none of it.
EMPTY_AND_ALONE = "907"

SIGNUP: dict[str, Any] = {
    "business_name": "Sunset Salon",
    "business_type": "salon",
    "services": "cuts, color",
    "operating_hours": "Mon-Fri 9-6",
    "greeting_style": "friendly",
    "escalation_rules": "Text the owner for emergencies",
    "notification_email": "owner@sunsetsalon.example.com",
    "area_code": "212",
    "plan": "starter",
    "contact_phone": "8055550142",
}


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------
async def test_numbers_in_the_requested_area_code_are_offered() -> None:
    result = await NumberSearchService(FakeTwilioProvider()).search(STOCKED)

    assert result.exact_match is True
    assert result.strategy == STRATEGY_EXACT
    assert result.offers
    assert {offer.area_code for offer in result.offers} == {STOCKED}


async def test_no_alternatives_when_the_requested_code_has_numbers() -> None:
    """The fallback is a fallback. A customer who can have 212 sees only 212."""
    result = await NumberSearchService(FakeTwilioProvider()).search(STOCKED)

    assert all(offer.e164.startswith(f"+1{STOCKED}") for offer in result.offers)


async def test_alternatives_are_offered_when_the_code_is_empty() -> None:
    result = await NumberSearchService(FakeTwilioProvider()).search(EMPTY_WITH_NEIGHBOURS)

    assert result.exact_match is False
    assert result.strategy == STRATEGY_SAME_STATE
    assert result.offers
    assert EMPTY_WITH_NEIGHBOURS not in {offer.area_code for offer in result.offers}


async def test_alternatives_are_geographic_rather_than_numeric() -> None:
    """916 is California, so 917 — New York — must never be suggested.

    This is the whole reason the fallback asks which state the code belongs to
    instead of doing arithmetic on the digits.
    """
    result = await NumberSearchService(FakeTwilioProvider()).search(EMPTY_WITH_NEIGHBOURS)

    offered = {offer.area_code for offer in result.offers}
    assert "917" not in offered
    assert offered <= {"213", "310", "415", "628"}


def test_alternatives_are_spread_across_area_codes() -> None:
    """Six numbers from one exchange is a worse choice than two from three.

    Tested on the spreading directly: what a vendor happens to return first is
    its business, and the fake returns a single code's inventory in order.
    """
    pool = [
        AvailableNumber(e164=f"+1{code}555{1000 + index:04d}")
        for code in ("213", "310", "415")
        for index in range(4)
    ]

    spread = _spread(pool, 6)

    assert len(spread) == 6
    assert {area_code_of(number.e164) for number in spread} == {"213", "310", "415"}


async def test_nothing_nearby_is_reported_honestly() -> None:
    result = await NumberSearchService(FakeTwilioProvider()).search(EMPTY_AND_ALONE)

    assert result.exact_match is False
    assert result.strategy == STRATEGY_NONE
    assert result.offers == []


# ---------------------------------------------------------------------------
# The signup contract
# ---------------------------------------------------------------------------
def test_choosing_a_number_is_optional() -> None:
    assert SignupRequest.model_validate(SIGNUP).selected_number == ""


def test_a_chosen_number_is_kept_verbatim() -> None:
    request = SignupRequest.model_validate({**SIGNUP, "selected_number": "+12125551000"})
    assert request.selected_number == "+12125551000"


@pytest.mark.parametrize("value", ["2125551000", "+1212555100", "+442071838750", "not a number"])
def test_a_number_that_is_not_ours_to_buy_is_refused(value: str) -> None:
    with pytest.raises(ValidationError):
        SignupRequest.model_validate({**SIGNUP, "selected_number": value})


# ---------------------------------------------------------------------------
# The endpoint (skips without PostgreSQL)
# ---------------------------------------------------------------------------
async def test_the_endpoint_offers_numbers_to_choose_from(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/numbers/available", params={"area_code": STOCKED})

    assert response.status_code == 200
    body = response.json()
    assert body["exact_match"] is True
    assert body["requested_area_code"] == STOCKED
    assert body["numbers"]
    assert all(number["area_code"] == STOCKED for number in body["numbers"])


async def test_the_endpoint_falls_back_to_nearby_numbers(api_client: AsyncClient) -> None:
    response = await api_client.get(
        "/api/v1/numbers/available", params={"area_code": EMPTY_WITH_NEIGHBOURS}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["exact_match"] is False
    assert body["numbers"]


async def test_an_area_code_that_does_not_exist_is_refused(api_client: AsyncClient) -> None:
    """ "999 is not an area code" reads very differently from "999 has none"."""
    response = await api_client.get("/api/v1/numbers/available", params={"area_code": "999"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


async def test_a_malformed_area_code_is_refused(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/numbers/available", params={"area_code": "21"})

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Buying what was chosen (skips without PostgreSQL)
# ---------------------------------------------------------------------------
async def _signup(api_client: AsyncClient, **overrides: Any) -> uuid.UUID:
    response = await api_client.post("/api/v1/signups", json={**SIGNUP, **overrides})
    assert response.status_code == 201
    return uuid.UUID(response.json()["tenant_id"])


async def _drive_to_active(api_app: FastAPI, ticks: int = 12) -> None:
    from app.worker import Worker

    worker = Worker(api_app.state.settings, api_app.state.providers, api_app.state.session_factory)
    for _ in range(ticks):
        await worker.tick()


async def _active_number(api_app: FastAPI, tenant_id: uuid.UUID) -> PhoneNumber:
    async with api_app.state.session_factory() as session:
        return (
            await session.execute(
                select(PhoneNumber)
                .where(PhoneNumber.tenant_id == tenant_id)
                .where(PhoneNumber.status == PhoneNumberStatus.ACTIVE)
            )
        ).scalar_one()


async def test_the_number_the_customer_chose_is_the_one_bought(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    chosen = "+12125551003"
    tenant_id = await _signup(api_client, selected_number=chosen)

    await _drive_to_active(api_app)

    number = await _active_number(api_app, tenant_id)
    assert number.e164 == chosen
    assert number.area_code == "212"


async def test_a_number_taken_in_the_meantime_falls_back_to_a_search(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Nothing is reserved between the search and the purchase.

    Losing the race must cost the customer a different number, not a failed
    signup — so the run continues down the existing ladder.
    """
    chosen = "+12125551002"
    # Someone else buys it first.
    await api_app.state.providers.twilio.purchase_number(
        e164=chosen, friendly_name="tenant:someone-else"
    )
    tenant_id = await _signup(api_client, selected_number=chosen)

    await _drive_to_active(api_app)

    number = await _active_number(api_app, tenant_id)
    assert number.e164 != chosen
    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
    assert tenant is not None
    assert tenant.status is TenantStatus.ACTIVE


async def test_a_nearby_number_records_its_own_area_code(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The row must describe the number bought, not the one that was asked for."""
    chosen = "+13105551000"
    tenant_id = await _signup(api_client, area_code="916", selected_number=chosen)

    await _drive_to_active(api_app)

    number = await _active_number(api_app, tenant_id)
    assert number.e164 == chosen
    assert number.area_code == "310"
