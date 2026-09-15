"""M16 — observability.

Two things are being tested, and the second is the one that gets forgotten.

**That the instrumentation exists and is correct.** Metrics that silently stop
recording are worse than none, because the dashboard keeps drawing a flat line
and everyone reads it as "nothing is failing".

**That the instrumentation leaks nothing.** Logs and metrics are the least
guarded surface in a deployment — shipped to a third-party aggregator, read by
support staff, exposed on an unauthenticated port. A credential or a transcript
that reaches them has escaped every other control in the system. So there are
explicit tests that API keys, caller numbers and conversation content do not
appear, and that label cardinality stays bounded.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.logging import configure_logging, get_logger
from app.core.metrics import MetricsRegistry, _safe_label
from app.models.enums import PhoneNumberStatus, TenantStatus
from app.services.signatures import build_twilio_signature, form_encode
from tests.factories import make_phone_number, make_tenant

AUTH_TOKEN = "test-token"


# ===========================================================================
# The metric primitives
# ===========================================================================


def test_a_counter_accumulates_per_label_set() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("things_total", "things")

    counter.inc(outcome="ok")
    counter.inc(outcome="ok")
    counter.inc(outcome="failed")

    rendered = registry.render()
    assert 'things_total{outcome="ok"} 2' in rendered
    assert 'things_total{outcome="failed"} 1' in rendered


def test_a_histogram_records_buckets_sum_and_count() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram("latency_seconds", "latency")

    histogram.observe(0.02)
    histogram.observe(0.4)
    histogram.observe(12.0)

    rendered = registry.render()
    assert "latency_seconds_count 3" in rendered
    # Cumulative: everything at or below the edge.
    assert 'latency_seconds_bucket{le="0.05"} 1' in rendered
    assert 'latency_seconds_bucket{le="0.5"} 2' in rendered
    assert 'latency_seconds_bucket{le="+Inf"} 3' in rendered


def test_a_timed_block_is_recorded_even_when_it_raises() -> None:
    """A failure's duration is the interesting one during an incident.

    "It hangs for thirty seconds and then fails" and "it fails instantly" are
    different problems; an untimed error path hides the difference.
    """
    registry = MetricsRegistry()
    histogram = registry.histogram("work_seconds", "work")

    try:
        with histogram.time(outcome="boom"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert 'work_seconds_count{outcome="boom"} 1' in registry.render()


def test_every_metric_is_declared_before_its_first_event() -> None:
    """A panel reading 0 means "nothing failed"; "no data" means "unknown".

    That distinction should not have to be made at 3am, so the metrics are
    registered at startup rather than on first use.
    """
    rendered = MetricsRegistry().render()
    for name in (
        "provisioning_steps_total",
        "provisioning_runs_total",
        "provider_calls_total",
        "webhook_deliveries_total",
        "inbound_calls_total",
        "voice_init_duration_seconds",
        "notifications_total",
    ):
        assert f"# HELP {name}" in rendered
        assert f"# TYPE {name}" in rendered


# ===========================================================================
# Cardinality — an unbounded label set is an outage
# ===========================================================================


def test_a_long_label_value_is_truncated() -> None:
    """A vendor error message as a label is a new time series per message."""
    assert len(_safe_label("x" * 500)) <= 64


def test_a_label_value_cannot_break_the_exposition_format() -> None:
    """Label values come from vendors; quotes and newlines must not escape."""
    registry = MetricsRegistry()
    registry.counter("odd_total", "odd").inc(reason='he said "no"\nthen left')

    rendered = registry.render()
    assert '\\"no\\"' in rendered
    # One metric line, not two — a raw newline would have split the record.
    assert len([line for line in rendered.splitlines() if line.startswith("odd_total{")]) == 1


def test_identifiers_are_not_used_as_labels() -> None:
    """Tenant and call ids belong in logs and traces, never in labels.

    A tenant id label produces one time series per customer forever, which
    kills the scraper first and the database second.
    """
    import inspect

    from app.api.v1 import voice

    source = inspect.getsource(voice)
    assert "inc(tenant_id" not in source
    assert "inc(call_sid" not in source


# ===========================================================================
# The endpoint
# ===========================================================================


async def test_metrics_are_exposed_in_prometheus_format(api_client: AsyncClient) -> None:
    response = await api_client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE webhook_deliveries_total counter" in response.text


async def test_the_metrics_endpoint_is_not_in_the_public_schema(
    api_client: AsyncClient,
) -> None:
    """An operational surface for the platform, not part of the API contract."""
    schema = (await api_client.get("/openapi.json")).json()
    assert "/metrics" not in schema["paths"]


async def test_metrics_expose_no_identifiers(api_client: AsyncClient, api_app: FastAPI) -> None:
    """The endpoint is unauthenticated; it must carry counts and nothing else."""
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(status=TenantStatus.ACTIVE, name="Sunset Salon")
        session.add(tenant)
        await session.flush()
        number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
        session.add(number)
        await session.commit()
        tenant_id, e164 = str(tenant.id), number.e164

    params = {"To": e164, "From": "+15551110000", "CallSid": "CA_metrics"}
    header = build_twilio_signature(
        url="http://testserver/api/v1/voice/inbound", params=params, auth_token=AUTH_TOKEN
    )
    await api_client.post(
        "/api/v1/voice/inbound",
        content=form_encode(params),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "x-twilio-signature": header,
        },
    )

    body = (await api_client.get("/metrics")).text
    assert tenant_id not in body
    assert e164 not in body
    assert "+15551110000" not in body
    assert "CA_metrics" not in body
    # The call was still counted, by disposition.
    assert "inbound_calls_total{" in body


async def test_the_voice_init_budget_is_measured(api_client: AsyncClient, api_app: FastAPI) -> None:
    """The only latency a caller can hear gets its own histogram."""
    await api_client.post("/api/v1/voice/init", json={"agent_number": "+14155550999"})

    body = (await api_client.get("/metrics")).text
    assert "voice_init_duration_seconds_count" in body
    # Bucketed around the 300ms budget rather than on generic edges.
    assert 'voice_init_duration_seconds_bucket{le="0.3"}' in body


# ===========================================================================
# Logs leak nothing
# ===========================================================================


def test_structured_logs_carry_a_correlation_id(capsys: pytest.CaptureFixture[str]) -> None:
    """The thread you pull when reconstructing an incident.

    Asserted against the stream the process actually writes, because that is
    what a log aggregator receives. The id is pulled from the request context by
    the formatter rather than passed by the caller, which is what makes it
    present on every line instead of on the lines someone remembered.
    """
    from app.core.correlation import new_correlation_id, reset_correlation_id, set_correlation_id

    configure_logging(level="INFO", fmt="json")
    logger = get_logger("test.correlation")

    correlation_id = new_correlation_id()
    token = set_correlation_id(correlation_id)
    try:
        logger.info("something happened", extra={"step": "validate"})
    finally:
        reset_correlation_id(token)

    written = capsys.readouterr()
    payloads = [
        json.loads(line)
        for line in (written.out + written.err).splitlines()
        if line.strip().startswith("{")
    ]
    assert any(payload.get("correlation_id") == correlation_id for payload in payloads)
    # Structured fields, not interpolated into the message text.
    assert any(payload.get("step") == "validate" for payload in payloads)


async def test_an_inbound_call_does_not_log_the_callers_number(
    api_client: AsyncClient,
    api_app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The caller is not our customer, and their number is theirs.

    It is kept on the call row under a retention policy. A log aggregator has
    no such policy.
    """
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(status=TenantStatus.ACTIVE)
        session.add(tenant)
        await session.flush()
        number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
        session.add(number)
        await session.commit()
        e164 = number.e164

    caller = "+15557654321"
    params = {"To": e164, "From": caller, "CallSid": "CA_privacy"}
    header = build_twilio_signature(
        url="http://testserver/api/v1/voice/inbound", params=params, auth_token=AUTH_TOKEN
    )

    with caplog.at_level(logging.INFO):
        await api_client.post(
            "/api/v1/voice/inbound",
            content=form_encode(params),
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "x-twilio-signature": header,
            },
        )

    assert caller not in caplog.text


def test_no_credential_shape_appears_in_the_settings_log() -> None:
    """Settings are logged at startup; secrets are `SecretStr` for this reason."""
    from pydantic import SecretStr

    from tests.support import build_settings

    settings = build_settings(
        anthropic_api_key="sk-ant-super-secret",
        twilio_auth_token="twilio-secret",
    )
    rendered = repr(settings)

    assert "sk-ant-super-secret" not in rendered
    assert "twilio-secret" not in rendered
    assert isinstance(settings.anthropic_api_key, SecretStr)
