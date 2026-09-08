"""Correlation ids must exist on every request and be safe to log."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.correlation import (
    get_correlation_id,
    new_correlation_id,
    sanitize_correlation_id,
)

HEADER = "X-Correlation-ID"


async def test_id_is_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert len(response.headers[HEADER]) == 32


async def test_caller_supplied_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={HEADER: "run-abc-123"})
    assert response.headers[HEADER] == "run-abc-123"


@pytest.mark.parametrize(
    "hostile",
    ["short", "", "has spaces", "inject\r\nX-Evil: 1", "x" * 200, "semi;colon"],
)
async def test_unsafe_ids_are_replaced(client: AsyncClient, hostile: str) -> None:
    """An id reaches logs and response headers, so it is never trusted verbatim."""
    response = await client.get("/healthz", headers={HEADER: hostile})
    assert response.headers[HEADER] != hostile
    assert len(response.headers[HEADER]) == 32


def test_sanitize_accepts_safe_ids() -> None:
    assert sanitize_correlation_id("abc-123_ok.v1") == "abc-123_ok.v1"


def test_sanitize_replaces_none() -> None:
    assert len(sanitize_correlation_id(None)) == 32


def test_generated_ids_are_unique() -> None:
    assert new_correlation_id() != new_correlation_id()


async def test_id_is_visible_inside_the_endpoint(app: FastAPI) -> None:
    """The whole point: handlers and everything they call see the id."""

    @app.get("/_probe")
    async def probe() -> dict[str, str | None]:
        return {"seen": get_correlation_id()}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/_probe", headers={HEADER: "seen-inside-handler"})

    assert response.json() == {"seen": "seen-inside-handler"}


async def test_context_is_reset_between_requests(client: AsyncClient) -> None:
    first = await client.get("/healthz")
    second = await client.get("/healthz")
    assert first.headers[HEADER] != second.headers[HEADER]
    assert get_correlation_id() is None
