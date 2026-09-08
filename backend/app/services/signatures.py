"""Webhook signature verification.

An unsigned post-call endpoint is a free way to poison every customer's
dashboard: anyone who guesses a call id can inject a transcript and trigger an
email. Both verifiers below use constant-time comparison, and both are strict
about what they accept.

When no secret is configured the request is *not* rejected — it is accepted and
recorded with ``signature_valid=False``. That keeps a local DRY_RUN loop usable
while making an unverified production event visible in the audit trail rather
than indistinguishable from a verified one. The endpoint refuses unverified
events outright in production-like environments.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import urlencode

#: Deliveries older than this are refused, so a captured request cannot be
#: replayed indefinitely.
DEFAULT_TOLERANCE_S = 30 * 60


def verify_elevenlabs(
    *,
    payload: bytes,
    header: str,
    secret: str,
    tolerance_s: int = DEFAULT_TOLERANCE_S,
    now: float | None = None,
) -> bool:
    """Verify an ``ElevenLabs-Signature: t=<epoch>,v0=<hex>`` header.

    The signed material is ``"{timestamp}.{body}"``, so replaying a body with a
    fresh timestamp fails: the timestamp is inside the MAC.
    """
    if not secret or not header:
        return False

    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    timestamp, provided = parts.get("t"), parts.get("v0")
    if not timestamp or not provided:
        return False

    try:
        age = (time.time() if now is None else now) - int(timestamp)
    except ValueError:
        return False
    if age > tolerance_s or age < -tolerance_s:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, provided.removeprefix("v0="))


def verify_twilio(*, url: str, params: dict[str, str], header: str, auth_token: str) -> bool:
    """Verify Twilio's ``X-Twilio-Signature``.

    Twilio signs the full request URL with the POST parameters appended in
    alphabetical order, HMAC-SHA1, base64. Reconstructing the URL exactly is the
    fiddly part: behind a tunnel or proxy the URL this process sees is not the
    one Twilio signed, so the caller passes the public URL built by
    :func:`public_request_url`.
    """
    if not auth_token or not header:
        return False

    material = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), material.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, header)


def build_twilio_signature(*, url: str, params: dict[str, str], auth_token: str) -> str:
    """Produce a valid signature. Used by the tests, and by nothing else."""
    material = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), material.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_elevenlabs_signature(*, payload: bytes, secret: str, timestamp: int) -> str:
    """Produce a valid header. Used by the tests, and by nothing else."""
    mac = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v0={mac}"


def public_request_url(*, observed_url: str, path: str, query: str, public_base: str) -> str:
    """The URL Twilio actually signed.

    With no ``public_base`` configured this is just what the server saw, which is
    correct when nothing sits in front of it. With one, the scheme and host are
    replaced so that a request arriving through ngrok or a load balancer is
    verified against the address the caller really used.
    """
    if not public_base:
        return observed_url
    rebuilt = f"{public_base.rstrip('/')}{path}"
    return f"{rebuilt}?{query}" if query else rebuilt


def form_encode(params: dict[str, str]) -> str:
    """Stable form encoding, so a test can post exactly what it signed."""
    return urlencode(sorted(params.items()))
