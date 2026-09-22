"""The ``state`` parameter that carries an OAuth flow across the redirect.

OAuth's redirect lands on our callback with a ``code`` and whatever ``state`` we
sent. The state has two jobs, and both are security jobs:

* **Binding.** It says which tenant, which provider and which *user* started the
  flow, so the callback stores the credential on the right tenant. It is signed,
  so none of those can be edited in the address bar.
* **CSRF.** The callback also requires the browser's own session to belong to
  the user named in the state. Without that, an attacker could start a flow with
  *their* Google account and trick a customer's browser into completing it —
  silently connecting the attacker's calendar to the victim's receptionist, so
  bookings go to the attacker.

Short-lived (ten minutes: long enough for a consent screen, short enough that a
leaked URL is not reusable) and signed with a key derived for this purpose only,
so a status-page token can never be replayed as an OAuth state or vice versa.
"""

from __future__ import annotations

import hmac
import json
import secrets
import time
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256

from app.core.config import Settings
from app.core.errors import AuthenticationError, ConfigurationError
from app.models.enums import IntegrationProvider

STATE_TTL_S = 10 * 60
_PURPOSE = b"integration-oauth-state:v1"


@dataclass(frozen=True, slots=True)
class OAuthState:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    provider: IntegrationProvider


def _key(settings: Settings) -> bytes:
    secret = settings.auth_secret_key.get_secret_value()
    if not secret:
        raise ConfigurationError(
            "AUTH_SECRET_KEY is required to sign OAuth state",
            details={"setting": "auth_secret_key"},
        )
    # Domain separation: a key used only for this, derived from the shared one.
    return hmac.new(secret.encode("utf-8"), _PURPOSE, sha256).digest()


def _b64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_state(
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    provider: IntegrationProvider,
    now: float | None = None,
) -> str:
    payload = {
        "t": str(tenant_id),
        "u": str(user_id),
        "p": provider.value,
        "e": int((now if now is not None else time.time()) + STATE_TTL_S),
        # Makes every state unique, so two flows started in the same second are
        # distinguishable in logs and never share a value.
        "n": secrets.token_urlsafe(12),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_key(settings), body.encode(), sha256).digest())
    return f"{body}.{signature}"


def verify_state(
    settings: Settings,
    state: str,
    *,
    provider: IntegrationProvider,
    now: float | None = None,
) -> OAuthState:
    """Return what the state binds, or raise. One error for every failure."""
    refused = AuthenticationError("this connection link is invalid or has expired")
    body, _, signature = state.partition(".")
    if not body or not signature:
        raise refused

    expected = _b64(hmac.new(_key(settings), body.encode(), sha256).digest())
    # Signature first: nothing from an unverified body is parsed or trusted.
    if not hmac.compare_digest(expected, signature):
        raise refused

    try:
        payload = json.loads(_unb64(body))
        granted = OAuthState(
            tenant_id=uuid.UUID(payload["t"]),
            user_id=uuid.UUID(payload["u"]),
            provider=IntegrationProvider(payload["p"]),
        )
        expires_at = int(payload["e"])
    except (ValueError, KeyError, TypeError) as exc:
        raise refused from exc

    if expires_at <= (now if now is not None else time.time()):
        raise refused
    # A state minted for Google must not complete an Outlook callback.
    if granted.provider is not provider:
        raise refused
    return granted
