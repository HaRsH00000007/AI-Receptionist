"""POST /api/v1/signups, against the real database."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from app.models import BusinessProfile, ProvisioningRun, ProvisioningStepRecord, Tenant
from app.models.enums import STEP_SEQUENCE, ProvisioningStatus, StepStatus, TenantStatus

SIGNUP_URL = "/api/v1/signups"


def payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "business_name": "Sunset Salon",
        "business_type": "salon",
        "services": "cuts, color, walk-ins",
        "operating_hours": "Mon-Fri 9-6, Sat till 2, closed Sun",
        "greeting_style": "friendly",
        "escalation_rules": "Text the owner if someone says it is an emergency",
        "notification_email": "owner@sunsetsalon.example.com",
        "area_code": "805",
        "plan": "starter",
        "contact_phone": "(805) 555-0142",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
async def test_valid_signup_is_created(api_client: AsyncClient) -> None:
    response = await api_client.post(SIGNUP_URL, json=payload())

    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["business_name"] == "Sunset Salon"
    assert body["tenant_status"] == TenantStatus.PENDING.value
    assert body["provisioning_status"] == ProvisioningStatus.DRAFT.value
    # Derived from the area code, never asked of a model.
    assert body["timezone"] == "America/Los_Angeles"
    assert body["correlation_id"]


async def test_signup_persists_the_whole_graph(api_client: AsyncClient, api_app: FastAPI) -> None:
    """Tenant, profile, run and all seven steps land in one transaction."""
    response = await api_client.post(SIGNUP_URL, json=payload())
    tenant_id = response.json()["tenant_id"]

    async with api_app.state.session_factory() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
        profile = (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == tenant_id)
            )
        ).scalar_one()
        run = (
            await session.execute(
                select(ProvisioningRun).where(ProvisioningRun.tenant_id == tenant_id)
            )
        ).scalar_one()
        steps = (
            (
                await session.execute(
                    select(ProvisioningStepRecord).where(ProvisioningStepRecord.run_id == run.id)
                )
            )
            .scalars()
            .all()
        )

    assert tenant.contact_phone == "+18055550142"
    assert tenant.contact_email == "owner@sunsetsalon.example.com"
    assert profile.services == ["cuts", "color", "walk-ins"]
    # Parsed at signup, so the LLM does not have to re-read it later.
    assert profile.hours_json is not None
    assert profile.raw_form_json["business_name"] == "Sunset Salon"

    # The whole plan is visible from the first second, not built as it happens.
    assert len(steps) == len(STEP_SEQUENCE)
    assert {step.status for step in steps} == {StepStatus.PENDING}
    assert len({step.idempotency_key for step in steps}) == len(STEP_SEQUENCE)


async def test_email_and_phone_are_normalized(api_client: AsyncClient) -> None:
    response = await api_client.post(
        SIGNUP_URL,
        json=payload(
            notification_email="Owner@SunsetSalon.EXAMPLE.COM", contact_phone="805-555-0142"
        ),
    )
    assert response.json()["contact_email"] == "owner@sunsetsalon.example.com"


async def test_unparseable_hours_are_kept_raw_for_the_llm(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Signup must not fail because hours are written oddly."""
    response = await api_client.post(
        SIGNUP_URL, json=payload(operating_hours="whenever the surf is flat")
    )
    assert response.status_code == 201

    async with api_app.state.session_factory() as session:
        profile = (
            await session.execute(
                select(BusinessProfile).where(
                    BusinessProfile.tenant_id == response.json()["tenant_id"]
                )
            )
        ).scalar_one()
    assert profile.hours_json is None
    assert profile.hours_raw == "whenever the surf is flat"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("notification_email", "not-an-email"),
        ("notification_email", ""),
        ("business_type", "bakery"),
        ("greeting_style", "sarcastic"),
        ("plan", "platinum"),
        ("business_name", ""),
        ("operating_hours", ""),
    ],
)
async def test_invalid_fields_are_rejected(api_client: AsyncClient, field: str, value: str) -> None:
    response = await api_client.post(SIGNUP_URL, json=payload(**{field: value}))
    assert response.status_code == 422


async def test_trial_plan_cannot_be_requested(api_client: AsyncClient) -> None:
    """It is an internal default, not something the form may ask for."""
    response = await api_client.post(SIGNUP_URL, json=payload(plan="trial"))
    assert response.status_code == 422


@pytest.mark.parametrize("phone", ["123", "+44 20 7946 0958", "0055550142"])
async def test_invalid_phone_is_rejected_with_a_named_field(
    api_client: AsyncClient, phone: str
) -> None:
    response = await api_client.post(SIGNUP_URL, json=payload(contact_phone=phone))
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "contact_phone"


@pytest.mark.parametrize("area_code", ["99", "199", "999"])
async def test_invalid_area_code_is_rejected(api_client: AsyncClient, area_code: str) -> None:
    response = await api_client.post(SIGNUP_URL, json=payload(area_code=area_code))
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "area_code"


async def test_unknown_fields_are_refused(api_client: AsyncClient) -> None:
    """A renamed form field must fail loudly, not be silently dropped."""
    response = await api_client.post(SIGNUP_URL, json=payload(referral_code="ABC"))
    assert response.status_code == 422


async def test_empty_services_are_refused(api_client: AsyncClient) -> None:
    response = await api_client.post(SIGNUP_URL, json=payload(services="  , ; "))
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
async def test_resubmitting_the_form_does_not_create_a_second_tenant(
    api_client: AsyncClient,
) -> None:
    """Definition of done 4: the same form twice must not buy two numbers."""
    first = await api_client.post(SIGNUP_URL, json=payload())
    second = await api_client.post(SIGNUP_URL, json=payload())

    assert first.status_code == 201
    assert first.json()["created"] is True
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["tenant_id"] == first.json()["tenant_id"]
    assert second.json()["run_id"] == first.json()["run_id"]


async def test_duplicate_detection_is_case_insensitive(api_client: AsyncClient) -> None:
    await api_client.post(SIGNUP_URL, json=payload())
    second = await api_client.post(
        SIGNUP_URL, json=payload(notification_email="OWNER@SunsetSalon.example.com")
    )
    assert second.json()["created"] is False


async def test_a_different_business_still_gets_its_own_tenant(
    api_client: AsyncClient,
) -> None:
    first = await api_client.post(SIGNUP_URL, json=payload())
    second = await api_client.post(
        SIGNUP_URL, json=payload(notification_email="owner@otherplace.example.com")
    )
    assert second.status_code == 201
    assert second.json()["tenant_id"] != first.json()["tenant_id"]


async def test_only_one_run_exists_after_a_duplicate(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    await api_client.post(SIGNUP_URL, json=payload())
    await api_client.post(SIGNUP_URL, json=payload())

    async with api_app.state.session_factory() as session:
        runs = (await session.execute(select(ProvisioningRun))).scalars().all()
    assert len(runs) == 1


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
async def test_signups_are_rate_limited(api_client: AsyncClient, api_app: FastAPI) -> None:
    """Every accepted signup eventually spends money, so the form is gated."""
    api_app.state.signup_limiter.limit = 3
    api_app.state.signup_limiter.reset()

    codes = [
        (
            await api_client.post(
                SIGNUP_URL, json=payload(notification_email=f"owner{index}@salon.example.com")
            )
        ).status_code
        for index in range(5)
    ]

    assert codes[:3] == [201, 201, 201]
    assert codes[3:] == [429, 429]


# ---------------------------------------------------------------------------
# Tally adapter
# ---------------------------------------------------------------------------
def tally_body(**overrides: str) -> dict[str, Any]:
    fields = {
        "Business Name": "Bay Legal",
        "Business Type": "legal",
        "Services Offered": "wills, conveyancing",
        "Operating Hours": "Mon-Fri 9-5",
        "Greeting Style": "formal",
        "Escalation Rules": "Escalate anything urgent",
        "Notification Email": "clerk@baylegal.example.com",
        "Area Code": "415",
        "Plan": "pro",
        "Contact Phone": "4155550188",
    }
    fields.update(overrides)
    return {
        "eventId": "evt_tally_1",
        "data": {
            "fields": [
                {"label": label, "key": label.lower(), "value": value}
                for label, value in fields.items()
            ]
        },
    }


async def test_tally_submission_becomes_a_signup(api_client: AsyncClient) -> None:
    response = await api_client.post(f"{SIGNUP_URL}/tally", json=tally_body())

    assert response.status_code == 201
    body = response.json()
    assert body["business_name"] == "Bay Legal"
    assert body["contact_email"] == "clerk@baylegal.example.com"
    assert body["timezone"] == "America/Los_Angeles"


async def test_tally_choice_fields_arrive_as_lists(api_client: AsyncClient) -> None:
    body = tally_body()
    for field in body["data"]["fields"]:
        if field["label"] == "Business Type":
            field["value"] = ["legal"]
    response = await api_client.post(f"{SIGNUP_URL}/tally", json=body)
    assert response.status_code == 201


async def test_tally_missing_fields_say_which(api_client: AsyncClient) -> None:
    body = tally_body()
    body["data"]["fields"] = [
        field for field in body["data"]["fields"] if field["label"] != "Notification Email"
    ]

    response = await api_client.post(f"{SIGNUP_URL}/tally", json=body)
    assert response.status_code == 422
    assert "notification_email" in response.json()["error"]["details"]["missing"]


async def test_tally_signature_is_checked_when_configured(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    api_app.state.settings.tally_signing_secret = SecretStr("shared-secret")
    try:
        rejected = await api_client.post(f"{SIGNUP_URL}/tally", json=tally_body())
        accepted = await api_client.post(
            f"{SIGNUP_URL}/tally",
            json=tally_body(),
            headers={"tally-signature": "shared-secret"},
        )
    finally:
        api_app.state.settings.tally_signing_secret = SecretStr("")

    assert rejected.status_code == 422
    assert accepted.status_code == 201
