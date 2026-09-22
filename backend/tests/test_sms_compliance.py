"""SMS compliance: nobody texts until review says so.

The rule this file exists to hold is narrow: a campaign may be sent only when
the registration is ``enabled``, and the customer has no way to get it there
themselves. Everything else — the states, the draft, the completeness check —
is in service of that.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.models.enums import MembershipRole, SmsRegistrationStatus
from app.models.sms import SmsRegistration
from app.services.sms_compliance import can_send, missing_for_submission
from tests.portal_support import Owner, add_member, live_owner, other_tenant, sign_up

COMPLETE: dict[str, Any] = {
    "brand_type": "standard",
    "legal_business_name": "Sunset Salon LLC",
    "tax_id": "12-3456789",
    "website": "sunsetsalon.example.com",
    "address_line1": "1 Ocean Ave",
    "city": "Santa Barbara",
    "region": "CA",
    "postal_code": "93101",
    "contact_name": "Dana Rivera",
    "contact_email": "dana@sunsetsalon.example.com",
    "contact_phone": "(805) 555-0142",
    "use_case": "appointment_reminders",
    "campaign_description": (
        "Appointment confirmations and reminders for clients who booked by phone."
    ),
    "sample_messages": [
        "Sunset Salon: your cut with Dana is confirmed for Fri 3pm. Reply STOP to opt out.",
        "Sunset Salon reminder: see you tomorrow at 3pm. Reply C to cancel.",
    ],
    "opt_in_description": (
        "Callers agree on the phone to receive texts about their booking, and the "
        "receptionist confirms their number before any text is sent."
    ),
}

REVIEW = "/api/v1/admin/tenants/{tenant_id}/sms/review"


def _url(owner: Owner, suffix: str = "") -> str:
    return f"/api/v1/tenants/{owner.tenant_id}/sms{suffix}"


async def _submitted(client: AsyncClient, api_app: FastAPI) -> Owner:
    owner = await live_owner(client, api_app)
    saved = await client.put(_url(owner, "/registration"), json=COMPLETE, headers=owner.headers)
    assert saved.status_code == 200, saved.text
    submitted = await client.post(_url(owner, "/registration/submit"), headers=owner.headers)
    assert submitted.status_code == 200, submitted.text
    return owner


async def _review(client: AsyncClient, owner: Owner, **decision: Any) -> Any:
    return await client.post(REVIEW.format(tenant_id=owner.tenant_id), json=decision)


# ---------------------------------------------------------------------------
# States a customer sees
# ---------------------------------------------------------------------------


async def test_before_the_number_is_live_sms_is_not_configured(api_client: AsyncClient) -> None:
    owner = await sign_up(api_client)  # still provisioning
    body = (await api_client.get(_url(owner), headers=owner.headers)).json()
    assert body == {
        "state": "not_configured",
        "phone_number": None,
        "can_send": False,
        "editable": False,
        "registration": None,
    }
    refused = await api_client.put(
        _url(owner, "/registration"), json=COMPLETE, headers=owner.headers
    )
    assert refused.status_code == 409


async def test_a_live_number_without_registration_needs_compliance(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    body = (await api_client.get(_url(owner), headers=owner.headers)).json()
    assert body["state"] == "compliance_required"
    assert body["phone_number"]
    assert body["can_send"] is False
    assert body["editable"] is True


async def test_a_draft_saves_partial_details_and_normalizes_them(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    response = await api_client.put(
        _url(owner, "/registration"),
        json={"legal_business_name": "  Sunset Salon LLC ", "website": "sunsetsalon.example.com"},
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "draft"
    assert body["registration"]["legal_business_name"] == "Sunset Salon LLC"
    assert body["registration"]["website"] == "https://sunsetsalon.example.com"
    assert body["can_send"] is False


@pytest.mark.parametrize(
    "change",
    [{"tax_id": "123"}, {"contact_phone": "12"}, {"contact_email": "not-an-email"}, {"x": 1}],
)
async def test_malformed_details_are_refused(
    api_client: AsyncClient, api_app: FastAPI, change: dict[str, Any]
) -> None:
    owner = await live_owner(api_client, api_app)
    response = await api_client.put(
        _url(owner, "/registration"), json={**COMPLETE, **change}, headers=owner.headers
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Submitting
# ---------------------------------------------------------------------------


async def test_an_incomplete_registration_cannot_be_submitted(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await api_client.put(
        _url(owner, "/registration"),
        json={**COMPLETE, "tax_id": None, "sample_messages": ["Too short"]},
        headers=owner.headers,
    )
    response = await api_client.post(_url(owner, "/registration/submit"), headers=owner.headers)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "sms_registration_incomplete"
    assert set(error["details"]["missing"]) == {"tax_id", "sample_messages"}


def test_a_sole_proprietor_needs_no_ein_but_samples_need_opt_out_language() -> None:
    registration = SmsRegistration(
        **{
            **COMPLETE,
            "brand_type": "sole_proprietor",
            "tax_id": None,
            "website": None,
            "contact_phone": "+18055550142",
            "sample_messages": [
                "Sunset Salon: your appointment is confirmed for Friday.",
                "Sunset Salon reminder: see you tomorrow at 3pm.",
            ],
        }
    )
    assert missing_for_submission(registration) == ["sample_messages_opt_out"]


async def test_a_complete_registration_is_submitted_and_locked(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await _submitted(api_client, api_app)
    body = (await api_client.get(_url(owner), headers=owner.headers)).json()
    assert body["state"] == "submitted"
    assert body["editable"] is False
    assert body["can_send"] is False
    assert body["registration"]["submitted_at"]

    # With review now, so the customer can neither edit nor resubmit.
    edit = await api_client.put(_url(owner, "/registration"), json=COMPLETE, headers=owner.headers)
    assert edit.status_code == 409
    again = await api_client.post(_url(owner, "/registration/submit"), headers=owner.headers)
    assert again.status_code == 409


async def test_a_plain_member_cannot_fill_in_the_registration(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    member = await add_member(api_app, owner.tenant_id, MembershipRole.MEMBER)
    response = await api_client.put(_url(owner, "/registration"), json=COMPLETE, headers=member)
    assert response.status_code == 403
    assert (await api_client.get(_url(owner), headers=member)).status_code == 200


async def test_another_tenant_cannot_read_the_registration(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await _submitted(api_client, api_app)
    _, stranger = await other_tenant(api_app)
    assert (await api_client.get(_url(owner), headers=stranger)).status_code == 404


# ---------------------------------------------------------------------------
# Review, and the gate
# ---------------------------------------------------------------------------


async def test_review_walks_forward_to_enabled_and_only_then_can_send(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await _submitted(api_client, api_app)

    reviewing = await _review(api_client, owner, status="under_review")
    assert reviewing.json()["state"] == "under_review"
    approved = await _review(api_client, owner, status="approved", twilio_brand_sid="BN123")
    assert approved.json()["state"] == "approved"
    # Approved is not enough to send: the number must be on the messaging service.
    assert approved.json()["can_send"] is False

    enabled = await _review(
        api_client, owner, status="enabled", twilio_messaging_service_sid="MG123"
    )
    assert enabled.status_code == 200, enabled.text
    body = enabled.json()
    assert body["state"] == "enabled"
    assert body["can_send"] is True
    # Twilio ids are ours; the customer's view never shows them.
    assert "twilio_messaging_service_sid" not in body["registration"]


@pytest.mark.parametrize(
    "decision",
    [
        {"status": "enabled", "twilio_messaging_service_sid": "MG1"},  # skips approval
        {"status": "draft"},  # review cannot un-submit
    ],
)
async def test_review_cannot_skip_a_step(
    api_client: AsyncClient, api_app: FastAPI, decision: dict[str, Any]
) -> None:
    owner = await _submitted(api_client, api_app)
    response = await _review(api_client, owner, **decision)
    assert response.status_code == 409


async def test_enabling_needs_the_messaging_service(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await _submitted(api_client, api_app)
    await _review(api_client, owner, status="approved")
    response = await _review(api_client, owner, status="enabled")
    assert response.status_code == 422


async def test_a_rejection_needs_a_reason_and_reopens_the_form(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await _submitted(api_client, api_app)
    assert (await _review(api_client, owner, status="rejected")).status_code == 422

    rejected = await _review(
        api_client,
        owner,
        status="rejected",
        rejection_reason="The website doesn't mention text messages.",
    )
    body = rejected.json()
    assert body["state"] == "rejected"
    assert body["editable"] is True
    assert body["registration"]["rejection_reason"].startswith("The website")

    # The customer fixes it and resubmits; the reason clears on resubmission.
    await api_client.put(_url(owner, "/registration"), json=COMPLETE, headers=owner.headers)
    resubmitted = await api_client.post(_url(owner, "/registration/submit"), headers=owner.headers)
    assert resubmitted.json()["state"] == "submitted"
    assert resubmitted.json()["registration"]["rejection_reason"] is None


def test_can_send_is_true_only_when_enabled() -> None:
    assert can_send(None) is False
    for status in SmsRegistrationStatus:
        assert can_send(SmsRegistration(status=status)) is (status is SmsRegistrationStatus.ENABLED)
