"""Liveness and readiness endpoints.

Unversioned and unprefixed on purpose: these are operational endpoints for the
platform, not part of the public API contract, and must not move when the API
version does.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status

from app import __version__
from app.api.deps import SettingsDep
from app.core.metrics import MetricsRegistry
from app.core.readiness import ReadinessRegistry

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe")
async def healthz(settings: SettingsDep) -> dict[str, Any]:
    """Is the process alive? Never touches a dependency."""
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
    }


@router.get("/readyz", summary="Readiness probe")
async def readyz(request: Request, response: Response) -> dict[str, Any]:
    """Can the process serve traffic? Consults every registered dependency."""
    registry: ReadinessRegistry = request.app.state.readiness
    results = await registry.run()

    checks = {r.name: {"ok": r.ok, "detail": r.detail, "critical": r.critical} for r in results}
    # Only a *critical* dependency can withdraw this replica from service. A
    # degraded cache is reported and stays serving; see `CheckResult.critical`.
    ready = all(r.ok for r in results if r.critical)
    degraded = [r.name for r in results if not r.ok and not r.critical]

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if ready else "not_ready",
        "checks": checks,
        # Named separately so an operator can tell "serving, but something is
        # wrong" from "serving, everything is fine" without diffing the checks.
        "degraded": degraded,
    }


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
async def metrics(request: Request, settings: SettingsDep) -> Response:
    """Scrape endpoint.

    Unauthenticated, like ``/healthz``, and deliberately kept out of the OpenAPI
    schema: it is an operational surface for the platform, not part of the
    public API. It exposes counts and durations only — never a tenant id, a
    caller's number or a vendor credential — because a metrics endpoint is the
    easiest thing in a deployment to accidentally expose.
    """
    registry: MetricsRegistry = request.app.state.metrics
    return Response(
        content=registry.render(),
        # The exact content type Prometheus expects; a plain text/plain scrape
        # is accepted but loses the version negotiation.
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
