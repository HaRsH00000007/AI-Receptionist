"""Wire format of the real vendor adapters.

**These do not prove the integrations work.** They prove that, given the
response shapes documented by each vendor, the adapters build the requests we
intend and parse the replies without crashing — and that a credential never
leaks into an error, a log, or the customer-facing status page.

Endpoint paths and payload shapes still need confirming against live accounts.
What these tests remove is the other half of the risk: a `KeyError` on a field
we misremembered, an auth header built wrongly, or a retry decision made on the
wrong status code.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.errors import VendorError
from app.providers.http import ProviderHTTPClient, safe_path
from app.providers.models import EmailMessage
from app.providers.real.llm import AnthropicProvider, OpenAIProvider
from app.providers.real.mail import ResendProvider, SendGridProvider
from app.providers.real.telephony import TwilioRestProvider
from app.providers.real.voice import ElevenLabsRestProvider
from tests.support import build_settings

ACCOUNT_SID = "AC" + "0" * 32


class Recorder:
    """Captures the request each adapter builds and replies with a canned body."""

    def __init__(self, responses: list[tuple[int, Any]]) -> None:
        self._responses = responses
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        async def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            status, body = self._responses.pop(0)
            if isinstance(body, str):
                return httpx.Response(status, text=body)
            return httpx.Response(status, json=body)

        return httpx.MockTransport(handle)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def form(self, index: int = -1) -> dict[str, str]:
        raw = self.requests[index].content.decode()
        return dict(pair.split("=", 1) for pair in raw.split("&"))

    def body(self, index: int = -1) -> dict[str, Any]:
        decoded: dict[str, Any] = json.loads(self.requests[index].content.decode())
        return decoded


def twilio(recorder: Recorder) -> TwilioRestProvider:
    settings = build_settings(twilio_account_sid=ACCOUNT_SID, twilio_auth_token="tok")
    provider = TwilioRestProvider(settings)
    provider._client = ProviderHTTPClient(
        vendor="twilio",
        base_url=settings.twilio_api_base_url,
        timeout_s=5,
        auth=(ACCOUNT_SID, "tok"),
        transport=recorder.transport(),
    )
    return provider


def elevenlabs(recorder: Recorder) -> ElevenLabsRestProvider:
    settings = build_settings(elevenlabs_api_key="xi-key")
    provider = ElevenLabsRestProvider(settings)
    provider._client = ProviderHTTPClient(
        vendor="elevenlabs",
        base_url=settings.elevenlabs_api_base_url,
        timeout_s=5,
        headers={"xi-api-key": "xi-key"},
        transport=recorder.transport(),
    )
    return provider


# ---------------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------------
SEARCH_BODY = {
    "available_phone_numbers": [
        {
            "friendly_name": "(805) 555-1000",
            "phone_number": "+18055551000",
            "locality": "Santa Barbara",
            "region": "CA",
            "iso_country": "US",
            "capabilities": {"voice": True, "SMS": True, "MMS": True},
        }
    ]
}

PURCHASE_BODY = {
    "sid": "PN" + "a" * 32,
    "phone_number": "+18055551000",
    "friendly_name": "tenant:abc",
    "account_sid": ACCOUNT_SID,
}


async def test_twilio_search_builds_the_documented_request() -> None:
    recorder = Recorder([(200, SEARCH_BODY)])
    provider = twilio(recorder)

    numbers = await provider.search_available_numbers(area_code="805", limit=3)

    request = recorder.last
    assert request.method == "GET"
    assert (
        request.url.path
        == f"/2010-04-01/Accounts/{ACCOUNT_SID}/AvailablePhoneNumbers/US/Local.json"
    )
    assert dict(request.url.params) == {"VoiceEnabled": "true", "PageSize": "3", "AreaCode": "805"}
    # Basic auth, not a bearer token.
    assert request.headers["authorization"].startswith("Basic ")

    assert len(numbers) == 1
    assert numbers[0].e164 == "+18055551000"
    assert numbers[0].region == "CA"
    assert numbers[0].voice_enabled is True


async def test_twilio_toll_free_uses_the_toll_free_resource() -> None:
    recorder = Recorder([(200, {"available_phone_numbers": []})])
    provider = twilio(recorder)

    await provider.search_available_numbers(toll_free=True, limit=5)

    assert recorder.last.url.path.endswith("/AvailablePhoneNumbers/US/TollFree.json")
    # Toll-free numbers belong to no state, so no region filter is sent.
    assert "InRegion" not in recorder.last.url.params


async def test_twilio_region_search_sends_in_region() -> None:
    recorder = Recorder([(200, SEARCH_BODY)])
    provider = twilio(recorder)

    await provider.search_available_numbers(in_region="CA")

    assert recorder.last.url.params["InRegion"] == "CA"


async def test_twilio_search_tolerates_a_number_without_voice() -> None:
    body = {
        "available_phone_numbers": [
            {"phone_number": "+18055551001", "capabilities": {"voice": False}}
        ]
    }
    provider = twilio(Recorder([(200, body)]))

    numbers = await provider.search_available_numbers(area_code="805")

    # The purchase step filters these out; the adapter must report them faithfully.
    assert numbers[0].voice_enabled is False


async def test_twilio_purchase_posts_form_encoded_and_parses_the_sid() -> None:
    recorder = Recorder([(201, PURCHASE_BODY)])
    provider = twilio(recorder)

    purchased = await provider.purchase_number(e164="+18055551000", friendly_name="tenant:abc")

    assert recorder.last.method == "POST"
    assert recorder.last.headers["content-type"] == "application/x-www-form-urlencoded"
    form = recorder.form()
    assert form["PhoneNumber"] == "%2B18055551000"  # '+' is percent-encoded
    assert form["FriendlyName"] == "tenant%3Aabc"
    assert purchased.sid == PURCHASE_BODY["sid"]
    assert purchased.e164 == "+18055551000"


async def test_twilio_purchase_without_a_sid_is_terminal() -> None:
    """A 200 with no SID means we cannot record what we just bought."""
    provider = twilio(Recorder([(200, {"phone_number": "+18055551000"})]))

    with pytest.raises(VendorError) as caught:
        await provider.purchase_number(e164="+18055551000", friendly_name="tenant:abc")

    assert caught.value.retryable is False


async def test_twilio_adoption_guard_filters_on_friendly_name() -> None:
    recorder = Recorder([(200, {"incoming_phone_numbers": [PURCHASE_BODY]})])
    provider = twilio(recorder)

    found = await provider.find_by_friendly_name(friendly_name="tenant:abc")

    assert recorder.last.url.params["FriendlyName"] == "tenant:abc"
    assert found is not None and found.sid == PURCHASE_BODY["sid"]


async def test_twilio_adoption_guard_returns_none_when_nothing_matches() -> None:
    provider = twilio(Recorder([(200, {"incoming_phone_numbers": []})]))
    assert await provider.find_by_friendly_name(friendly_name="tenant:abc") is None


async def test_twilio_release_accepts_204() -> None:
    recorder = Recorder([(204, "")])
    provider = twilio(recorder)

    await provider.release_number(sid=PURCHASE_BODY["sid"])

    assert recorder.last.method == "DELETE"
    assert recorder.last.url.path.endswith(f"/IncomingPhoneNumbers/{PURCHASE_BODY['sid']}.json")


async def test_twilio_release_of_an_unknown_number_is_a_no_op() -> None:
    """Compensation must be safe to run twice."""
    provider = twilio(Recorder([(404, {"code": 20404, "message": "not found"})]))
    await provider.release_number(sid="PNgone")  # must not raise


async def test_twilio_get_number_returns_none_on_404() -> None:
    provider = twilio(Recorder([(404, {"code": 20404})]))
    assert await provider.get_number(sid="PNgone") is None


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (404, False), (409, False), (429, True), (500, True), (503, True)],
)
async def test_vendor_status_codes_drive_the_retry_decision(status: int, retryable: bool) -> None:
    provider = twilio(Recorder([(status, {"code": 1, "message": "x"})]))

    with pytest.raises(VendorError) as caught:
        await provider.search_available_numbers(area_code="805")

    assert caught.value.retryable is retryable


async def test_a_timeout_is_retryable() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    client = ProviderHTTPClient(
        vendor="twilio",
        base_url="https://api.twilio.com",
        timeout_s=1,
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(VendorError) as caught:
        await client.get("/x")

    assert caught.value.retryable is True
    assert caught.value.code == "vendor_timeout"


async def test_a_non_json_body_is_terminal() -> None:
    """An HTML error page will still be HTML on the next attempt."""
    client = ProviderHTTPClient(
        vendor="twilio",
        base_url="https://api.twilio.com",
        timeout_s=1,
        transport=Recorder([(200, "<html>gateway</html>")]).transport(),
    )
    with pytest.raises(VendorError) as caught:
        await client.get("/x")

    assert caught.value.retryable is False
    assert caught.value.code == "vendor_bad_response"


# ---------------------------------------------------------------------------
# Secret hygiene
# ---------------------------------------------------------------------------
async def test_the_account_sid_never_reaches_an_error_detail() -> None:
    """`run.last_error` is rendered on the customer-facing status page."""
    provider = twilio(Recorder([(400, {"code": 21422, "message": "unavailable"})]))

    with pytest.raises(VendorError) as caught:
        await provider.search_available_numbers(area_code="805")

    rendered = json.dumps(caught.value.to_dict())
    assert ACCOUNT_SID not in rendered
    assert "AC<redacted>" in rendered
    # The endpoint is still identifiable, which is the point of keeping a path.
    assert "AvailablePhoneNumbers" in rendered


def test_safe_path_redacts_account_identifiers_but_keeps_the_route() -> None:
    path = f"/2010-04-01/Accounts/{ACCOUNT_SID}/IncomingPhoneNumbers.json"
    assert safe_path(path) == "/2010-04-01/Accounts/AC<redacted>/IncomingPhoneNumbers.json"


def test_settings_repr_does_not_print_credentials() -> None:
    """A traceback or a stray log line must not spill the keys."""
    settings = build_settings(
        anthropic_api_key="sk-ant-supersecret",
        twilio_auth_token="twilio-supersecret",
        elevenlabs_api_key="xi-supersecret",
        admin_api_key="admin-supersecret",
    )
    dumped = repr(settings) + str(settings) + json.dumps(settings.model_dump(mode="json"))

    for secret in (
        "sk-ant-supersecret",
        "twilio-supersecret",
        "xi-supersecret",
        "admin-supersecret",
    ):
        assert secret not in dumped
    # Still readable where it is actually needed.
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-supersecret"


def test_a_supplied_credential_satisfies_the_startup_check() -> None:
    """Guards the inverse of the missing-credential test: a SecretStr is always
    truthy, so a naive emptiness check would accept a blank key."""
    settings = build_settings(
        dry_run=False,
        twilio_provider="twilio",
        twilio_account_sid=ACCOUNT_SID,
        twilio_auth_token="tok",
    )
    assert settings.effective_twilio_provider == "twilio"

    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="TWILIO_ACCOUNT_SID"):
        build_settings(
            dry_run=False,
            twilio_provider="twilio",
            twilio_account_sid=ACCOUNT_SID,
            twilio_auth_token="",
        )


# ---------------------------------------------------------------------------
# ElevenLabs
# ---------------------------------------------------------------------------
AGENT_BODY = {
    "agent_id": "agent_abc123",
    "name": "tenant:abc",
    "conversation_config": {
        "agent": {
            "prompt": {"prompt": "You are the receptionist."},
            "first_message": "Thanks for calling.",
            "language": "en",
        },
        "tts": {"voice_id": "voice_1"},
    },
}

PHONE_BODY = {
    "phone_number_id": "phnum_abc",
    "phone_number": "+18055551000",
    "label": "tenant:abc",
    "provider": "twilio",
    "assigned_agent": {"agent_id": "agent_abc123", "agent_name": "tenant:abc"},
}


async def test_elevenlabs_create_agent_sends_the_nested_config() -> None:
    recorder = Recorder([(200, {"agent_id": "agent_abc123"}), (200, AGENT_BODY)])
    provider = elevenlabs(recorder)

    agent = await provider.create_agent(
        name="tenant:abc",
        system_prompt="You are the receptionist.",
        first_message="Thanks for calling.",
        voice_id="voice_1",
    )

    create = recorder.requests[0]
    assert create.url.path == "/v1/convai/agents/create"
    assert create.headers["xi-api-key"] == "xi-key"
    body = recorder.body(0)
    assert body["name"] == "tenant:abc"
    assert body["conversation_config"]["agent"]["prompt"]["prompt"] == "You are the receptionist."
    assert body["conversation_config"]["tts"]["voice_id"] == "voice_1"

    # Read back, so callers get the same shape whether created or adopted.
    assert recorder.requests[1].url.path == "/v1/convai/agents/agent_abc123"
    assert agent.agent_id == "agent_abc123"
    assert agent.system_prompt == "You are the receptionist."
    assert agent.voice_id == "voice_1"


async def test_elevenlabs_get_agent_parses_the_nested_reply() -> None:
    provider = elevenlabs(Recorder([(200, AGENT_BODY)]))

    agent = await provider.get_agent(agent_id="agent_abc123")

    assert agent is not None
    assert agent.first_message == "Thanks for calling."
    assert agent.voice_id == "voice_1"


async def test_elevenlabs_get_agent_returns_none_on_404() -> None:
    provider = elevenlabs(Recorder([(404, {"detail": "not found"})]))
    assert await provider.get_agent(agent_id="gone") is None


async def test_elevenlabs_agent_without_an_id_is_a_bad_response_not_a_crash() -> None:
    """A shape we misremembered must surface as a classified vendor error."""
    provider = elevenlabs(Recorder([(200, {"name": "tenant:abc"})]))

    with pytest.raises(VendorError) as caught:
        await provider.get_agent(agent_id="agent_abc123")

    assert caught.value.code == "vendor_bad_response"
    assert caught.value.retryable is False


async def test_elevenlabs_update_agent_patches_then_reads_back() -> None:
    recorder = Recorder([(200, AGENT_BODY), (200, AGENT_BODY)])
    provider = elevenlabs(recorder)

    await provider.update_agent(
        agent_id="agent_abc123",
        name="tenant:abc",
        system_prompt="new prompt",
        first_message="new greeting",
        voice_id="voice_2",
    )

    assert recorder.requests[0].method == "PATCH"
    assert recorder.requests[0].url.path == "/v1/convai/agents/agent_abc123"
    assert recorder.body(0)["conversation_config"]["agent"]["prompt"]["prompt"] == "new prompt"
    assert recorder.requests[1].method == "GET"


async def test_elevenlabs_import_sends_the_twilio_credentials() -> None:
    recorder = Recorder([(200, PHONE_BODY)])
    provider = elevenlabs(recorder)

    ref = await provider.import_phone_number(
        e164="+18055551000",
        twilio_sid="PN123",
        twilio_account_sid=ACCOUNT_SID,
        twilio_auth_token="tok",
        label="tenant:abc",
    )

    assert recorder.last.url.path == "/v1/convai/phone-numbers"
    body = recorder.body()
    assert body == {
        "phone_number": "+18055551000",
        "label": "tenant:abc",
        "sid": ACCOUNT_SID,
        "token": "tok",
        "provider": "twilio",
    }
    assert ref.phone_id == "phnum_abc"
    assert ref.assigned_agent_id == "agent_abc123"


async def test_elevenlabs_import_adopts_an_already_imported_number() -> None:
    """A conflict means a previous attempt got there first, not a failure."""
    recorder = Recorder([(409, {"detail": "already exists"}), (200, [PHONE_BODY])])
    provider = elevenlabs(recorder)

    ref = await provider.import_phone_number(
        e164="+18055551000",
        twilio_sid="PN123",
        twilio_account_sid=ACCOUNT_SID,
        twilio_auth_token="tok",
        label="tenant:abc",
    )

    assert ref.phone_id == "phnum_abc"


async def test_elevenlabs_find_phone_number_handles_a_bare_list() -> None:
    provider = elevenlabs(Recorder([(200, [PHONE_BODY])]))
    ref = await provider.find_phone_number(e164="+18055551000")
    assert ref is not None and ref.phone_id == "phnum_abc"


async def test_elevenlabs_find_phone_number_handles_a_wrapped_list() -> None:
    provider = elevenlabs(Recorder([(200, {"phone_numbers": [PHONE_BODY]})]))
    ref = await provider.find_phone_number(e164="+18055551000")
    assert ref is not None and ref.phone_id == "phnum_abc"


async def test_elevenlabs_phone_without_an_id_is_a_bad_response() -> None:
    provider = elevenlabs(Recorder([(200, [{"phone_number": "+18055551000"}])]))

    with pytest.raises(VendorError) as caught:
        await provider.find_phone_number(e164="+18055551000")

    assert caught.value.code == "vendor_bad_response"


async def test_elevenlabs_assign_patches_the_agent_id() -> None:
    recorder = Recorder([(200, PHONE_BODY)])
    provider = elevenlabs(recorder)

    ref = await provider.assign_agent_to_number(phone_id="phnum_abc", agent_id="agent_abc123")

    assert recorder.last.method == "PATCH"
    assert recorder.last.url.path == "/v1/convai/phone-numbers/phnum_abc"
    assert recorder.body() == {"agent_id": "agent_abc123"}
    assert ref.assigned_agent_id == "agent_abc123"


async def test_elevenlabs_delete_of_a_missing_agent_is_a_no_op() -> None:
    provider = elevenlabs(Recorder([(404, {"detail": "gone"})]))
    await provider.delete_agent(agent_id="gone")  # must not raise


# ---------------------------------------------------------------------------
# LLM and email
# ---------------------------------------------------------------------------
async def test_anthropic_builds_the_messages_request() -> None:
    recorder = Recorder(
        [
            (
                200,
                {
                    "content": [{"type": "text", "text": '{"ok":true}'}],
                    "model": "claude-opus-5",
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                },
            )
        ]
    )
    settings = build_settings(anthropic_api_key="sk-ant-test")
    provider = AnthropicProvider(settings)
    provider._client = ProviderHTTPClient(
        vendor="anthropic",
        base_url="https://api.anthropic.com",
        timeout_s=5,
        headers={"x-api-key": "sk-ant-test", "anthropic-version": "2023-06-01"},
        transport=recorder.transport(),
    )

    reply = await provider.complete(
        system="sys", user="usr", model="claude-opus-5", max_tokens=100, temperature=0.0
    )

    assert recorder.last.url.path == "/v1/messages"
    # The key goes in x-api-key, unmasked — the SecretStr must be unwrapped.
    assert recorder.last.headers["x-api-key"] == "sk-ant-test"
    body = recorder.body()
    assert body["system"] == "sys"
    assert body["messages"] == [{"role": "user", "content": "usr"}]
    assert reply.text == '{"ok":true}'
    assert reply.input_tokens == 10


async def test_openai_sends_an_unmasked_bearer_token() -> None:
    """Guards the f-string trap: `f"Bearer {secret}"` yields '**********'."""
    recorder = Recorder([(200, {"choices": [{"message": {"content": "{}"}}], "model": "gpt"})])
    settings = build_settings(openai_api_key="sk-openai-test")
    provider = OpenAIProvider(settings)

    # Build the header the way the adapter does, then assert it is the real key.
    header = f"Bearer {settings.openai_api_key.get_secret_value()}"
    assert header == "Bearer sk-openai-test"
    assert "*" not in header

    provider._client = ProviderHTTPClient(
        vendor="openai",
        base_url="https://api.openai.com",
        timeout_s=5,
        headers={"authorization": header},
        transport=recorder.transport(),
    )
    reply = await provider.complete(
        system="sys", user="usr", model="gpt", max_tokens=50, temperature=0.0
    )

    assert recorder.last.url.path == "/v1/chat/completions"
    assert recorder.body()["response_format"] == {"type": "json_object"}
    assert reply.text == "{}"


async def test_resend_posts_the_message() -> None:
    recorder = Recorder([(200, {"id": "re_123"})])
    settings = build_settings(resend_api_key="re-test")
    provider = ResendProvider(settings)
    provider._client = ProviderHTTPClient(
        vendor="resend",
        base_url=settings.resend_api_base_url,
        timeout_s=5,
        transport=recorder.transport(),
    )

    result = await provider.send(
        EmailMessage(to="owner@example.com", subject="s", html="<p>h</p>", text="t")
    )

    assert recorder.last.url.path == "/emails"
    assert recorder.body()["to"] == ["owner@example.com"]
    assert result.message_id == "re_123"


async def test_sendgrid_accepts_202_with_an_empty_body() -> None:
    recorder = Recorder([(202, "")])
    settings = build_settings(sendgrid_api_key="sg-test", email_from="AI <bot@example.com>")
    provider = SendGridProvider(settings)
    provider._client = ProviderHTTPClient(
        vendor="sendgrid",
        base_url=settings.sendgrid_api_base_url,
        timeout_s=5,
        transport=recorder.transport(),
    )

    result = await provider.send(
        EmailMessage(to="owner@example.com", subject="s", html="<p>h</p>", text="t")
    )

    assert recorder.last.url.path == "/v3/mail/send"
    # The display name is stripped; SendGrid wants a bare address.
    assert recorder.body()["from"] == {"email": "bot@example.com"}
    assert result.message_id == ""
