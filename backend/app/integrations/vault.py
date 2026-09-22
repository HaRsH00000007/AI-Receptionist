"""Encryption at rest for the credentials a customer grants us.

An OAuth refresh token is a long-lived key to someone's calendar. It is
encrypted before it is written and decrypted only in the moment it is used, so
that what sits in PostgreSQL — and in every backup and replica of it — is
useless without ``INTEGRATION_ENCRYPTION_KEY``, which lives in the secret store
and nowhere else.

Fernet, from ``cryptography``: AES-128-CBC with an HMAC-SHA256 tag, so a
ciphertext that has been tampered with fails to decrypt rather than decrypting
to something else. ``MultiFernet`` makes rotation a configuration change: put the
new key first, keep the old one after it, and rows re-encrypt as they are next
written.

There is deliberately no plaintext fallback. With no key configured, connecting
an OAuth integration is refused — "configuration required" — rather than quietly
storing a token in the clear.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import Settings
from app.core.errors import ConfigurationError


def _keys(settings: Settings) -> list[str]:
    raw = settings.integration_encryption_key.get_secret_value()
    return [key.strip() for key in raw.split(",") if key.strip()]


def vault_configured(settings: Settings) -> bool:
    """True when at least one well-formed key is configured."""
    try:
        return bool(_build(settings))
    except ConfigurationError:
        return False


def _build(settings: Settings) -> MultiFernet | None:
    keys = _keys(settings)
    if not keys:
        return None
    try:
        return MultiFernet([Fernet(key.encode("ascii")) for key in keys])
    except (ValueError, UnicodeEncodeError) as exc:
        # Never echo the key. Saying which setting is malformed is enough.
        raise ConfigurationError(
            "INTEGRATION_ENCRYPTION_KEY is not a valid Fernet key",
            details={"setting": "integration_encryption_key"},
        ) from exc


class CredentialVault:
    def __init__(self, settings: Settings) -> None:
        fernet = _build(settings)
        if fernet is None:
            raise ConfigurationError(
                "INTEGRATION_ENCRYPTION_KEY is required to store integration credentials",
                details={"setting": "integration_encryption_key"},
            )
        self._fernet = fernet

    def encrypt(self, credentials: dict[str, Any]) -> bytes:
        return self._fernet.encrypt(json.dumps(credentials, separators=(",", ":")).encode())

    def decrypt(self, ciphertext: bytes) -> dict[str, Any]:
        try:
            plain = self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            # A rotated-away key or a tampered row. Either way the credential is
            # unusable and the customer has to reconnect; the error says which
            # setting to look at, not what the ciphertext contained.
            raise ConfigurationError(
                "stored integration credentials could not be decrypted",
                details={"setting": "integration_encryption_key"},
            ) from exc
        document = json.loads(plain)
        if not isinstance(document, dict):  # pragma: no cover - we only ever write dicts
            raise ConfigurationError("stored integration credentials are malformed")
        return document
