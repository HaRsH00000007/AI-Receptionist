"""Version 1 of the public API.

One aggregate router, mounted by the application factory under
``settings.api_v1_prefix``. Adding an endpoint means adding it here; nothing
reaches into the app object from a route module.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import admin, signups, tenants, webhooks

router = APIRouter()
router.include_router(signups.router)
router.include_router(tenants.router)
router.include_router(webhooks.router)
router.include_router(admin.router)

__all__ = ["router"]
