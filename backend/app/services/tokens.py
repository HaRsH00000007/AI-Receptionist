"""Bearer token generation and verification.

Two rules govern everything here, and both exist because of how these tokens
fail rather than how they work.

**Only hashes are stored.** The database holds SHA-256 of every token; the plain
value exists exactly once, in the response that issued it. A database dump
therefore hands over no usable session. This is the same reasoning as password
hashing, minus the need for a slow KDF: these tokens are 256 bits of ``os``
randomness, so there is no dictionary to attack and no benefit from bcrypt's
cost factor — only latency on every authenticated request.

**Comparisons are constant-time.** A short-circuiting ``==`` on a secret leaks
its prefix through timing, one byte at a time. :func:`hmac.compare_digest` is
used for every comparison of anything secret.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: 32 bytes → 43 URL-safe characters. Comfortably beyond brute force, and short
#: enough to survive an email client's line wrapping intact, which a longer
#: token does not reliably do.
_TOKEN_BYTES = 32


def generate_token() -> str:
    """A new, unguessable bearer token.

    ``secrets``, never ``random``: the latter is a Mersenne Twister seeded from
    the clock, and observing a few outputs is enough to predict the rest.
    """
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The stored form of ``token`` — SHA-256 hex, 64 characters.

    Deterministic and unsalted on purpose: a salted hash could not be looked up
    by index, which would turn every authenticated request into a table scan.
    Salt defends against precomputation over a small input space; a 256-bit
    random token has none.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(candidate: str, stored_hash: str) -> bool:
    """Constant-time comparison of a presented token against its stored hash."""
    return hmac.compare_digest(hash_token(candidate), stored_hash)


def fingerprint(payload: bytes) -> str:
    """A stable hash of a request body, for idempotency-key matching.

    Not a secret — this is an integrity check, so a plain digest is right and
    constant-time comparison is unnecessary.
    """
    return hashlib.sha256(payload).hexdigest()
