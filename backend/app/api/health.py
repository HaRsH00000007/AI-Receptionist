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

    checks = {r.name: {"ok": r.ok, "detail": r.detail} for r in results}
    ready = all(r.ok for r in results)

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ready" if ready else "not_ready", "checks": checks}
