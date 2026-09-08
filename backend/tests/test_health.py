"""Liveness must not depend on anything; readiness must depend on everything."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import __version__


async def test_healthz_reports_the_service(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "AI Receptionist API",
        "version": __version__,
        "environment": "test",
    }


async def test_readyz_is_ready_with_no_dependencies(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {}}


async def _get_readyz(app: FastAPI) -> tuple[int, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/readyz")
    return response.status_code, response.json()


async def test_readyz_passes_when_checks_pass(app: FastAPI) -> None:
    async def database() -> None:
        return None

    app.state.readiness.register("database", database)

    status_code, body = await _get_readyz(app)
    assert status_code == 200
    assert body == {"status": "ready", "checks": {"database": {"ok": True, "detail": None}}}


async def test_readyz_returns_503_when_a_check_fails(app: FastAPI) -> None:
    async def database() -> str:
        return "connection refused"

    app.state.readiness.register("database", database)

    status_code, body = await _get_readyz(app)
    assert status_code == 503
    assert body["status"] == "not_ready"
    assert body["checks"] == {"database": {"ok": False, "detail": "connection refused"}}


async def test_readyz_survives_a_check_that_raises(app: FastAPI) -> None:
    """A broken probe reports not-ready; it must not 500 the endpoint."""

    async def database() -> None:
        raise RuntimeError("driver exploded")

    app.state.readiness.register("database", database)

    status_code, body = await _get_readyz(app)
    assert status_code == 503
    assert body["checks"] == {"database": {"ok": False, "detail": "driver exploded"}}


async def test_healthz_stays_up_when_a_dependency_is_down(app: FastAPI) -> None:
    """The distinction that stops an orchestrator restarting a healthy process."""

    async def database() -> str:
        return "down"

    app.state.readiness.register("database", database)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/readyz")).status_code == 503
