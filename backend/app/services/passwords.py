"""Password hashing and verification.

Argon2id, with the library's defaults. Those defaults are chosen by people who
track the current cost of GPU cracking and are revised as hardware moves; a
hand-tuned cost factor here would be a snapshot of one afternoon's opinion and
would quietly rot.

Three properties matter, and each exists because of a specific failure:

**The hash is slow on purpose.** Unlike the bearer tokens in
:mod:`app.services.tokens`, a password is chosen by a person and comes from a
small, guessable space. SHA-256 over a human password is a dictionary attack
waiting for a database dump; a KDF with a real work factor is what makes that
dump uninteresting.

**Verification takes the same time whether or not the account exists.**
:func:`verify` is called with ``None`` for an unknown address and still does the
full Argon2 computation against :data:`_DUMMY_HASH`. Returning early there would
make a wrong password measurably slower than an unknown email, and the login
form would become a free oracle for "is this business a customer of yours?" —
the same leak the magic-link endpoint is careful to avoid.

**Rehashing is handled on the way in.** When the library's parameters change,
:func:`needs_rehash` reports it and the caller upgrades the stored hash during a
successful login, while the plain password is in hand. Without it, every hash
stays at the cost it was created with, forever.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

#: The shortest password accepted. Length beats composition rules: NIST dropped
#: the mixed-character requirements in 800-63B because they push people towards
#: `Passw0rd!` and a predictable shape.
MIN_LENGTH = 8

#: A cap, not a security control. Argon2 has no inherent length limit, but an
#: unbounded input is an unbounded amount of hashing work an anonymous caller
#: can ask for.
MAX_LENGTH = 128

_hasher = PasswordHasher()

#: Hashed once at import so that verifying against a nonexistent account costs
#: the same as verifying against a real one. The value is irrelevant — nothing
#: is ever expected to match it.
_DUMMY_HASH = _hasher.hash("argon2-timing-equalizer")


def hash_password(password: str) -> str:
    """Hash a password for storage. The plain value is never persisted."""
    return _hasher.hash(password)


def verify(password: str, stored_hash: str | None) -> bool:
    """Is ``password`` the one behind ``stored_hash``?

    ``stored_hash`` is ``None`` for an address with no account, and for a
    magic-link-only account that has never set a password. Both do the dummy
    comparison and return False, so the three cases are indistinguishable from
    the outside.
    """
    candidate = stored_hash or _DUMMY_HASH
    try:
        _hasher.verify(candidate, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, HashingError):
        return False
    # A real mismatch and a match against the dummy are the same answer.
    return stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    """True when this hash was made with parameters weaker than today's."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        # Unreadable rather than outdated. Say no: the caller would rehash from
        # a password it has already verified against this same broken value,
        # which cannot happen.
        return False
