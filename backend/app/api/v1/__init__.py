"""Version 1 of the public API.

One aggregate router, mounted by the application factory under
``settings.api_v1_prefix``. Adding an endpoint means adding it here; nothing
reaches into the app object from a route module.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    integrations,
    numbers,
    onboarding,
    portal,
    signups,
    sms,
    tenants,
    voice,
    webhooks,
)

router = APIRouter()
# Auth first: it is the only unauthenticated group, and reading it first
# makes the boundary between public and protected routes obvious.
router.include_router(auth.router)
# Public like signup, and for the same reason: the form offers numbers to
# choose from before anyone has an account. It searches and never buys.
router.include_router(numbers.router)
router.include_router(signups.router)
# The same form for someone already signed in: the account-first flow.
router.include_router(onboarding.router)
router.include_router(tenants.router)
# The signed-in portal. Same `/tenants/{id}` prefix, but membership-only: none
# of it is reachable with the status grant that opens the post-signup page.
router.include_router(portal.router)
router.include_router(integrations.router)
router.include_router(sms.router)
router.include_router(webhooks.router)
# The audible call path. Unauthenticated like the webhooks, and verified the
# same way — Twilio's signature is what stops anyone choosing whose agent answers.
router.include_router(voice.router)
router.include_router(admin.router)

__all__ = ["router"]
