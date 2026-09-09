"""Signed, expiring grants for the post-signup status page.

There is a real gap in the product flow: a business submits the signup form and
immediately wants to watch provisioning progress, but no user account exists yet
and nothing has been verified. The POC filled that gap by treating the tenant's
v4 UUID as an unguessable capability — the id in the URL *was* the credential.

That fails in the ways capability-URLs always fail. A UUID leaks through
``Referer`` headers, browser history, shared screenshots, support tickets and
server logs; it never expires; it cannot be revoked; and because the same id is
also the primary key used everywhere else, one leak is permanent full access to
that customer's calls and transcripts.

This replaces it with a proper grant: HMAC-signed, tenant-scoped, and expiring.
Same convenience — a link that works without logging in — without any of those
four properties.

    v1.<tenant-uuid>.<expiry-epoch>.<signature>

The signature covers the tenant and the expiry, so neither can be edited. The
token is not stored: it is verifiable from the secret alone, which keeps the
status page off the database's critical path and means there is no table to
prune. The cost is that an individual token cannot be revoked before expiry —
acceptable for a short-lived, read-only grant, and the reason the TTL is days
rather than months.
"""

from __future__ import annotations

import hmac
import time
import uuid
from base64 import urlsafe_b64encode
from hashlib import sha256

from app.core.config import Settings
from app.core.errors import AuthenticationError, ConfigurationError

_PREFIX = "v1"

#: Long enough to watch provisioning finish and come back to the link in an
#: email the next morning; short enough that a leaked URL stops working.
DEFAULT_STATUS_TOKEN_TTL_S = 7 * 24 * 60 * 60


def _signing_key(settings: Settings) -> bytes:
    secret = settings.auth_secret_key.get_secret_value()
    if not secret:
        # Refused rather than defaulted. A signing key that falls back to a
        # constant produces tokens anyone can forge, and does it silently.
        raise ConfigurationError(
            "AUTH_SECRET_KEY is required to sign status tokens",
            details={"setting": "auth_secret_key"},
        )
    return secret.encode("utf-8")


def _sign(key: bytes, payload: str) -> str:
    digest = hmac.new(key, payload.encode("utf-8"), sha256).digest()
    return urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_status_token(
    settings: Settings,
    tenant_id: uuid.UUID,
    *,
    ttl_s: int = DEFAULT_STATUS_TOKEN_TTL_S,
    now: float | None = None,
) -> str:
    """Mint a read-only grant for one tenant's status page."""
    expires_at = int((now if now is not None else time.time()) + ttl_s)
    payload = f"{_PREFIX}.{tenant_id}.{expires_at}"
    return f"{payload}.{_sign(_signing_key(settings), payload)}"


def verify_status_token(
    settings: Settings,
    token: str,
    *,
    now: float | None = None,
) -> uuid.UUID:
    """Return the tenant this token grants access to, or raise.

    Every failure raises the same error with the same message. Distinguishing
    "malformed" from "bad signature" from "expired" tells a forger which half of
    their attempt to keep working on.
    """
    moment = now if now is not None else time.time()
    parts = token.split(".")

    if len(parts) != 4 or parts[0] != _PREFIX:
        raise AuthenticationError("invalid or expired status link")

    _, raw_tenant, raw_expiry, signature = parts
    payload = f"{_PREFIX}.{raw_tenant}.{raw_expiry}"

    # Signature first, before anything derived from the payload is trusted or
    # parsed. Reading the expiry from an unverified token would mean acting on
    # attacker-controlled input.
    if not hmac.compare_digest(_sign(_signing_key(settings), payload), signature):
        raise AuthenticationError("invalid or expired status link")

    try:
        expires_at = int(raw_expiry)
        tenant_id = uuid.UUID(raw_tenant)
    except ValueError as exc:
        raise AuthenticationError("invalid or expired status link") from exc

    if expires_at <= moment:
        raise AuthenticationError("invalid or expired status link")

    return tenant_id
