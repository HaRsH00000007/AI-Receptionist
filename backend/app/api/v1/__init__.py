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
    contacts,
    greetings,
    integrations,
    numbers,
    onboarding,
    portal,
    receptionists,
    signups,
    sms,
    tags,
    tenants,
    transfers,
    voice,
    voice_tools,
    webhooks,
    website,
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
# The other way in: read the business's website and draft a receptionist from
# it. Same destination, same provisioning pipeline, no second way to spend.
router.include_router(website.router)
router.include_router(tenants.router)
# The signed-in portal. Same `/tenants/{id}` prefix, but membership-only: none
# of it is reachable with the status grant that opens the post-signup page.
router.include_router(portal.router)
# The one account-scoped read in the portal: every receptionist this user
# has, for the gallery. Scoped by the membership join, not by a path value.
router.include_router(receptionists.router)
# Contacts: the same /tenants/{id} prefix, membership to read and admin to
# write, like the rest of the portal.
router.include_router(contacts.router)
# Transfer rules: the Agent page's own section, same prefix, admin to write
# because a rule is what makes the product dial a real person.
router.include_router(transfers.router)
# Scheduled greetings: the other half of the same section, and admin to write
# for the same reason — it is what a caller hears first, spoken verbatim.
router.include_router(greetings.router)
# Call tags: the same section again, admin to write. Unlike the two above,
# a tag publishes nothing — it is read after a call, never during one.
router.include_router(tags.router)
router.include_router(integrations.router)
router.include_router(sms.router)
router.include_router(webhooks.router)
# The audible call path. Unauthenticated like the webhooks, and verified the
# same way — Twilio's signature is what stops anyone choosing whose agent answers.
router.include_router(voice.router)
# Called by the voice agent mid-call. Unauthenticated by session; each
# business's tools carry their own credential, checked before anything is read.
router.include_router(voice_tools.router)
router.include_router(admin.router)

__all__ = ["router"]
