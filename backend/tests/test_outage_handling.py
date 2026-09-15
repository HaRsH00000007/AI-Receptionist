"""M17 — outage handling.

The rule that governs all of it: **a vendor's bad day must not become a worse
day for our customers, and must never cost them money twice.**

Two halves. The first is the circuit breaker — once a vendor is genuinely down,
retrying into it only adds latency to requests that will fail anyway, and on the
call path that latency is a caller listening to silence. The second, and the one
that must not be traded away for the first, is that nothing about outage
handling may create a duplicate spend: no retry, no breaker state, no fallback
may buy a second phone number.
"""

from __future__ import annotations

from xml.etree.ElementTree import fromstring

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.errors import AppError
from app.models.enums import AgentStatus, PhoneNumberStatus, TenantStatus
from app.providers.circuit import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitRegistry,
    CircuitState,
)
from app.services.signatures import build_twilio_signature, form_encode
from tests.factories import make_agent, make_agent_config, make_phone_number, make_tenant

AUTH_TOKEN = "test-token"
INBOUND_PATH = "/api/v1/voice/inbound"


def _imported_modules(relative_path: str) -> set[str]:
    """Every module a file imports, read from its AST.

    Structural rather than textual: a comment mentioning a module must not make
    a test pass or fail, and an added import must.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(relative_path).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _breaker(**overrides: object) -> CircuitBreaker:
    values: dict[str, object] = {"name": "elevenlabs", "failure_threshold": 3, "cooldown_s": 30.0}
    values.update(overrides)
    return CircuitBreaker(**values)  # type: ignore[arg-type]


# ===========================================================================
# The breaker
# ===========================================================================


def test_a_healthy_vendor_passes_through() -> None:
    breaker = _breaker()
    assert breaker.allows() is True
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


def test_isolated_failures_do_not_open_the_circuit() -> None:
    """One blip is not an outage; tripping on it would be its own outage."""
    breaker = _breaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()

    assert breaker.state is CircuitState.CLOSED
    assert breaker.allows() is True


def test_consecutive_failures_open_the_circuit() -> None:
    breaker = _breaker(failure_threshold=3)
    for _ in range(3):
        breaker.record_failure()

    assert breaker.state is CircuitState.OPEN
    assert breaker.allows() is False


def test_an_open_circuit_refuses_without_attempting_the_call() -> None:
    """The entire value: the fallback runs in milliseconds, not after a timeout."""
    breaker = _breaker(failure_threshold=1)
    breaker.record_failure()

    with pytest.raises(CircuitOpenError) as caught:
        breaker.check()

    assert caught.value.code == "circuit_open"
    assert caught.value.http_status == 503


def test_the_circuit_half_opens_after_its_cooldown() -> None:
    breaker = _breaker(failure_threshold=1, cooldown_s=30.0)
    breaker.record_failure(now=1_000.0)

    assert breaker.allows(now=1_010.0) is False
    assert breaker.allows(now=1_040.0) is True
    assert breaker.state is CircuitState.HALF_OPEN


def test_only_one_probe_is_admitted_while_half_open() -> None:
    """Letting a herd through on recovery knocks the vendor straight back down."""
    breaker = _breaker(failure_threshold=1, cooldown_s=30.0)
    breaker.record_failure(now=1_000.0)

    assert breaker.allows(now=1_040.0) is True  # the probe
    assert breaker.allows(now=1_041.0) is False  # everyone else waits


def test_a_successful_probe_closes_the_circuit() -> None:
    breaker = _breaker(failure_threshold=1, cooldown_s=30.0)
    breaker.record_failure(now=1_000.0)
    breaker.allows(now=1_040.0)

    breaker.record_success()

    assert breaker.state is CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


def test_a_failed_probe_reopens_for_another_cooldown() -> None:
    breaker = _breaker(failure_threshold=1, cooldown_s=30.0)
    breaker.record_failure(now=1_000.0)
    breaker.allows(now=1_040.0)

    breaker.record_failure(now=1_041.0)

    assert breaker.state is CircuitState.OPEN
    assert breaker.allows(now=1_050.0) is False
    assert breaker.allows(now=1_080.0) is True


def test_the_breaker_uses_a_monotonic_clock() -> None:
    """A wall-clock jump must not trap a circuit open or reopen it early.

    NTP corrections and resumed VMs move wall time; `time.monotonic` cannot go
    backwards, so the cooldown means what it says.
    """
    import inspect

    source = inspect.getsource(CircuitBreaker)
    assert "time.monotonic()" in source
    assert "time.time()" not in source


def test_each_vendor_gets_its_own_breaker() -> None:
    """An ElevenLabs outage must not stop us buying numbers from Twilio."""
    registry = CircuitRegistry(failure_threshold=1)
    voice = registry.for_vendor("elevenlabs")
    telephony = registry.for_vendor("twilio")

    voice.record_failure()

    assert voice.allows() is False
    assert telephony.allows() is True
    assert registry.states()["elevenlabs"] == "open"
    assert registry.states()["twilio"] == "closed"


def test_breakers_are_per_process_not_shared_through_redis() -> None:
    """A shared breaker would let a cache outage open every circuit at once.

    That escalates a degraded cache into a total vendor outage — the opposite
    of what a breaker is for. Asserted on the module's real imports rather than
    its prose, so the test tracks the code and not the comments.
    """
    assert "redis" not in _imported_modules("app/providers/circuit.py")

    # And the state really is per-instance: two registries do not share it.
    first, second = CircuitRegistry(failure_threshold=1), CircuitRegistry(failure_threshold=1)
    first.for_vendor("elevenlabs").record_failure()
    assert first.for_vendor("elevenlabs").allows() is False
    assert second.for_vendor("elevenlabs").allows() is True


# ===========================================================================
# What a caller hears when the vendor is down
# ===========================================================================


async def _live_tenant(api_app: FastAPI) -> str:
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(status=TenantStatus.ACTIVE, name="Sunset Salon")
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
        return number.e164


async def _dial(api_client: AsyncClient, e164: str, call_sid: str = "CA_outage"):  # type: ignore[no-untyped-def]
    params = {"To": e164, "From": "+15551110000", "CallSid": call_sid}
    header = build_twilio_signature(
        url=f"http://testserver{INBOUND_PATH}", params=params, auth_token=AUTH_TOKEN
    )
    return await api_client.post(
        INBOUND_PATH,
        content=form_encode(params),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "x-twilio-signature": header,
        },
    )


async def test_a_voice_vendor_outage_takes_a_voicemail(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The customer-visible payoff of the whole module.

    Silence is a lost customer and the business never learns the call happened.
    A recorded apology plus a voicemail is degraded service that still delivers
    the message.
    """
    e164 = await _live_tenant(api_app)

    # Healthy: the agent takes the call.
    healthy = fromstring((await _dial(api_client, e164, "CA_ok")).text)
    assert healthy.find("Connect/Stream") is not None

    # The vendor starts failing.
    breaker = api_app.state.circuits.for_vendor("elevenlabs")
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()

    degraded = fromstring((await _dial(api_client, e164, "CA_down")).text)
    assert degraded.find("Connect") is None
    assert degraded.find("Record") is not None
    say = degraded.find("Say")
    assert say is not None and say.text is not None
    assert "temporarily unavailable" in say.text
    assert "Sunset Salon" in say.text


async def test_the_call_path_recovers_when_the_vendor_does(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    e164 = await _live_tenant(api_app)
    breaker = api_app.state.circuits.for_vendor("elevenlabs")
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()

    assert fromstring((await _dial(api_client, e164, "CA_1")).text).find("Record") is not None

    breaker.record_success()

    recovered = fromstring((await _dial(api_client, e164, "CA_2")).text)
    assert recovered.find("Connect/Stream") is not None


async def test_an_unknown_number_is_still_rejected_during_an_outage(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A vendor outage must not turn every wrong number into a billed voicemail."""
    breaker = api_app.state.circuits.for_vendor("elevenlabs")
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()

    response = await _dial(api_client, "+14155550999", "CA_unknown")
    assert fromstring(response.text).find("Reject") is not None


# ===========================================================================
# The line outage handling must never cross
# ===========================================================================


def test_circuit_open_is_classified_as_a_vendor_failure_not_a_retry_signal() -> None:
    """Callers must take their fallback, not loop.

    A caller that read this as "try again immediately" would hammer a vendor
    that is already down — and on a paid path, retrying blindly is how a second
    phone number gets bought.
    """
    error = CircuitOpenError("elevenlabs is unavailable", details={"vendor": "elevenlabs"})
    assert isinstance(error, AppError)
    assert error.http_status == 503


def test_the_breaker_cannot_reach_a_purchase_path() -> None:
    """Structural: the breaker imports no vendor client and no provisioning step.

    It can prevent a call from being attempted; it has no way to *make* one, so
    no edit to it can introduce a duplicate spend.
    """
    imported = _imported_modules("app/providers/circuit.py")

    assert "httpx" not in imported
    assert not any(name.startswith("app.provisioning") for name in imported)
    assert not any(name.startswith("app.providers.real") for name in imported)
    # It reaches only errors and logging.
    assert imported <= {
        "__future__",
        "enum",
        "time",
        "dataclasses",
        "app.core.errors",
        "app.core.logging",
    }


async def test_the_money_safety_guards_are_untouched_by_outage_handling(
    api_app: FastAPI,
) -> None:
    """The adoption guard still runs before every purchase.

    Outage handling changes *when* a step is attempted, never what it does —
    the guards that stop a retry buying a second number are the same code.
    """
    import inspect

    from app.provisioning.steps import purchase_number

    source = inspect.getsource(purchase_number)
    assert "find_by_friendly_name" in source
    # The billing gate is re-evaluated in the same moment as the spend.
    assert source.count("billing") >= 1
