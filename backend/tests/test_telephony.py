"""M6 — the production telephony path.

Every assertion here is something a real person on a phone would experience.
The governing rule is that **a caller must never hear silence**: each branch
ends in a working agent, a spoken explanation, or a voicemail, and the tests are
organized around proving there is no fourth outcome.

The second theme is cross-tenant routing. The dialled number chooses whose agent
answers, so an unsigned request that could set it would let anyone route a call
into another business's receptionist.
"""

from __future__ import annotations

import uuid
from xml.etree.ElementTree import fromstring

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.models.enums import AgentStatus, PhoneNumberStatus, TenantStatus
from app.services.signatures import build_twilio_signature, form_encode
from app.telephony import twiml
from app.telephony.routing import (
    CallDisposition,
    RoutingInput,
    decide,
    vendor_outage_decision,
)
from tests.factories import (
    make_agent,
    make_agent_config,
    make_phone_number,
    make_tenant,
)

AUTH_TOKEN = "test-token"
INBOUND_PATH = "/api/v1/voice/inbound"


def _routing(**overrides: object) -> RoutingInput:
    values: dict[str, object] = {
        "dialled_e164": "+14155550100",
        "tenant_id": uuid.uuid4(),
        "tenant_status": TenantStatus.ACTIVE,
        "agent_id": "agent_abc",
        "business_name": "Sunset Salon",
    }
    values.update(overrides)
    return RoutingInput(**values)  # type: ignore[arg-type]


def _post_inbound(client: AsyncClient, params: dict[str, str]):  # type: ignore[no-untyped-def]
    header = build_twilio_signature(
        url=f"http://testserver{INBOUND_PATH}", params=params, auth_token=AUTH_TOKEN
    )
    return client.post(
        INBOUND_PATH,
        content=form_encode(params),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "x-twilio-signature": header,
        },
    )


# ===========================================================================
# The routing decision
# ===========================================================================


def test_a_healthy_tenant_reaches_its_agent() -> None:
    decision = decide(_routing())
    assert decision.disposition is CallDisposition.CONNECT_AGENT
    assert decision.agent_id == "agent_abc"


def test_a_number_nobody_owns_is_refused_without_answering() -> None:
    """The one case where not answering is right.

    Answering costs a billed minute and confirms to a scanner that the line is
    live. Every *other* branch answers, because those numbers belong to a real
    business that may already be advertising them.
    """
    decision = decide(_routing(tenant_id=None))
    assert decision.disposition is CallDisposition.REJECT


def test_a_tenant_without_an_agent_still_gets_a_voicemail() -> None:
    """Provisioning may not have finished, but the number is already dialable.

    Dropping this call would lose a customer's customer over an internal state
    they cannot see.
    """
    decision = decide(_routing(agent_id=None))
    assert decision.disposition is CallDisposition.VOICEMAIL
    assert "leave a message" in decision.message.lower()
    assert "Sunset Salon" in decision.message


def test_a_pending_tenant_with_an_agent_is_served_normally() -> None:
    """A number can be dialled before the welcome email arrives."""
    decision = decide(_routing(tenant_status=TenantStatus.PENDING))
    assert decision.disposition is CallDisposition.CONNECT_AGENT


@pytest.mark.parametrize("status", [TenantStatus.CANCELLED, TenantStatus.ABANDONED])
def test_a_former_customer_is_told_plainly(status: TenantStatus) -> None:
    """A voicemail nobody will ever read is worse than a clear ending."""
    decision = decide(_routing(tenant_status=status))
    assert decision.disposition is CallDisposition.UNAVAILABLE
    assert "no longer in service" in decision.message


def test_a_failed_tenant_takes_a_message_rather_than_dropping_the_call() -> None:
    """The business's caller should not absorb our provisioning problem."""
    decision = decide(_routing(tenant_status=TenantStatus.FAILED))
    assert decision.disposition is CallDisposition.VOICEMAIL


def test_a_vendor_outage_takes_a_message(_unused: None = None) -> None:
    """An explicit branch, not an exception handler that looks similar."""
    decision = vendor_outage_decision(_routing())
    assert decision.disposition is CallDisposition.VOICEMAIL
    assert decision.reason == "vendor_outage"
    assert "temporarily unavailable" in decision.message


def test_every_disposition_is_reachable() -> None:
    """No branch of the thing a customer hears is dead code."""
    reached = {
        decide(_routing()).disposition,
        decide(_routing(tenant_id=None)).disposition,
        decide(_routing(agent_id=None)).disposition,
        decide(_routing(tenant_status=TenantStatus.CANCELLED)).disposition,
    }
    assert reached == set(CallDisposition)


# ===========================================================================
# TwiML construction — XML injection is remote control of a phone call
# ===========================================================================


def test_a_business_name_cannot_restructure_the_document() -> None:
    """The reason this module uses ElementTree rather than f-strings.

    A signup form supplies the business name. If it could close a tag and open
    a <Dial>, Twilio would place a call to a number of the attacker's choosing
    and bill it to the tenant.
    """
    hostile = "Salon</Say><Dial>+15559999999</Dial><Say>"
    document = twiml.say_and_record(message=f"Thanks for calling {hostile}")

    root = fromstring(document)
    assert root.tag == "Response"
    assert root.find("Dial") is None
    # It survives as inert text inside the spoken message.
    say = root.find("Say")
    assert say is not None and say.text is not None
    assert "Dial" in say.text


def test_a_hostile_caller_id_cannot_inject_a_parameter() -> None:
    document = twiml.connect_stream(
        websocket_url="wss://example.test/stream?agent_id=a1",
        parameters={"call_sid": 'CA1"/><Dial>+1555</Dial><Parameter name="x'},
    )
    root = fromstring(document)
    assert root.find("Dial") is None
    assert root.find("Connect/Stream") is not None


def test_connect_stream_is_bidirectional() -> None:
    """<Connect><Stream> lets the agent speak; <Start><Stream> only listens."""
    root = fromstring(twiml.connect_stream(websocket_url="wss://example.test/s"))
    assert root.find("Connect/Stream") is not None
    assert root.find("Start") is None


def test_stream_parameters_carry_identifiers_not_configuration() -> None:
    """This document reaches Twilio's logs and the websocket handshake.

    A prompt or a token here would be sprayed across two vendors' infrastructure
    for no benefit — the far end can look the tenant up by id.
    """
    tenant_id = str(uuid.uuid4())
    document = twiml.connect_stream(
        websocket_url="wss://example.test/s",
        parameters={"tenant_id": tenant_id, "call_sid": "CA1"},
    )
    assert "system_prompt" not in document
    assert tenant_id in document


def test_a_voicemail_document_speaks_before_it_records() -> None:
    """Recording without an announcement is a beep into the void."""
    root = fromstring(twiml.say_and_record(message="Please leave a message", max_length_s=90))
    children = [child.tag for child in root]
    assert children == ["Say", "Record"]
    record = root.find("Record")
    assert record is not None
    assert record.get("maxLength") == "90"


def test_a_very_long_message_is_truncated() -> None:
    root = fromstring(twiml.say_and_record(message="word " * 2000))
    say = root.find("Say")
    assert say is not None and say.text is not None
    assert len(say.text) <= 600


def test_every_builder_emits_a_parseable_response_document() -> None:
    """An unparseable document is silence on a live call."""
    documents = [
        twiml.connect_stream(websocket_url="wss://example.test/s"),
        twiml.say_and_record(message="hello"),
        twiml.say_and_hangup(message="goodbye"),
        twiml.reject(),
    ]
    for document in documents:
        assert document.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert fromstring(document).tag == "Response"


# ===========================================================================
# The endpoint
# ===========================================================================


async def test_an_inbound_call_to_a_live_tenant_connects_its_agent(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(status=TenantStatus.ACTIVE)
        session.add(tenant)
        await session.flush()
        config = make_agent_config(tenant, is_live=True)
        session.add(config)
        await session.flush()
        number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
        session.add_all(
            [
                number,
                make_agent(
                    tenant,
                    config,
                    status=AgentStatus.ACTIVE,
                    elevenlabs_agent_id="agent_live",
                ),
            ]
        )
        await session.commit()

    response = await _post_inbound(
        api_client,
        {"To": number.e164, "From": "+15551110000", "CallSid": "CA_live"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    root = fromstring(response.text)
    stream = root.find("Connect/Stream")
    assert stream is not None
    assert "agent_live" in (stream.get("url") or "")


async def test_an_unsigned_inbound_call_cannot_choose_a_tenant(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The cross-tenant routing guard.

    The dialled number decides whose agent answers, so it must come from a
    request Twilio actually signed.
    """
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(status=TenantStatus.ACTIVE)
        session.add(tenant)
        await session.flush()
        number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
        session.add(number)
        await session.commit()

    response = await api_client.post(
        INBOUND_PATH,
        content=form_encode({"To": number.e164, "From": "+1555", "CallSid": "CA_forged"}),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "x-twilio-signature": "obviously-wrong",
        },
    )

    assert response.status_code == 403
    root = fromstring(response.text)
    assert root.find("Reject") is not None
    assert root.find("Connect") is None


async def test_an_unknown_number_is_rejected(api_client: AsyncClient) -> None:
    response = await _post_inbound(
        api_client, {"To": "+14155550999", "From": "+15551110000", "CallSid": "CA_x"}
    )
    assert response.status_code == 200
    assert fromstring(response.text).find("Reject") is not None


async def test_a_tenant_without_an_agent_gets_voicemail_over_http(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(status=TenantStatus.PENDING, name="Half Provisioned Ltd")
        session.add(tenant)
        await session.flush()
        number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
        session.add(number)
        await session.commit()

    response = await _post_inbound(
        api_client, {"To": number.e164, "From": "+15551110000", "CallSid": "CA_pending"}
    )

    root = fromstring(response.text)
    assert root.find("Record") is not None
    say = root.find("Say")
    assert say is not None and say.text is not None
    assert "Half Provisioned Ltd" in say.text


async def test_a_malformed_inbound_request_still_answers_in_twiml(
    api_client: AsyncClient,
) -> None:
    """Never JSON. Twilio cannot read it and would play nothing."""
    response = await _post_inbound(api_client, {"CallSid": "CA_empty"})
    assert response.headers["content-type"].startswith("application/xml")
    assert fromstring(response.text).tag == "Response"


# ===========================================================================
# /voice/init — the dynamic variables that make a shared agent specific
# ===========================================================================


async def test_init_serves_the_tenants_own_configuration(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(status=TenantStatus.ACTIVE, name="Sunset Salon")
        session.add(tenant)
        await session.flush()
        session.add(
            make_agent_config(
                tenant,
                is_live=True,
                version=7,
                first_message="Thanks for calling Sunset Salon.",
            )
        )
        number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
        session.add(number)
        await session.commit()

    response = await api_client.post("/api/v1/voice/init", json={"agent_number": number.e164})

    assert response.status_code == 200
    variables = response.json()["dynamic_variables"]
    assert variables["business_name"] == "Sunset Salon"
    assert variables["greeting"] == "Thanks for calling Sunset Salon."
    assert variables["config_version"] == "7"


async def test_init_never_serves_another_tenants_variables(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A salon's agent must not answer a law firm's call."""
    async with api_app.state.session_factory() as session:
        salon = make_tenant(status=TenantStatus.ACTIVE, name="Sunset Salon")
        firm = make_tenant(status=TenantStatus.ACTIVE, name="Careful Legal")
        session.add_all([salon, firm])
        await session.flush()
        salon_number = make_phone_number(salon, status=PhoneNumberStatus.ACTIVE)
        firm_number = make_phone_number(firm, status=PhoneNumberStatus.ACTIVE)
        session.add_all([salon_number, firm_number])
        await session.commit()

    response = await api_client.post("/api/v1/voice/init", json={"agent_number": firm_number.e164})
    variables = response.json()["dynamic_variables"]
    assert variables["business_name"] == "Careful Legal"
    assert "Sunset" not in str(variables)


async def test_init_falls_back_to_a_safe_generic_configuration(
    api_client: AsyncClient,
) -> None:
    """The conversation has already started; an error here is silence.

    A generic but competent receptionist still hears the caller and still takes
    a message, which is a far better outcome than a failed conversation.
    """
    response = await api_client.post("/api/v1/voice/init", json={"agent_number": "+14155550999"})

    assert response.status_code == 200
    variables = response.json()["dynamic_variables"]
    assert variables["business_name"]
    assert variables["greeting"]


async def test_init_survives_an_unparseable_body(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/voice/init",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200
    assert "dynamic_variables" in response.json()


# ===========================================================================
# The POC path is preserved
# ===========================================================================


def test_the_poc_call_path_remains_the_default() -> None:
    """Existing tenants keep the native ElevenLabs path until migrated."""
    from tests.support import build_settings

    assert build_settings().call_path == "elevenlabs_native"
    assert build_settings(call_path="twiml_stream").call_path == "twiml_stream"
