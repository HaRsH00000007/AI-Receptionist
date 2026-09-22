"""Request and response contracts for authentication.

DTOs, not models. Nothing here exposes a database row directly — in particular
nothing exposes ``token_hash``, ``is_platform_admin`` on another user, or any
internal id a client has no use for.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

from app.models.enums import MembershipRole


class MagicLinkRequest(BaseModel):
    """Ask for a login link."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class MagicLinkResponse(BaseModel):
    """Deliberately uninformative.

    The same body is returned whether or not an account exists, so the endpoint
    cannot be used to test whether an address is a customer. There is no
    ``sent`` boolean here for exactly that reason.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = "If that address has an account, a sign-in link is on its way."


class PasswordSignIn(BaseModel):
    """Sign in with an email address and a password."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    #: Bounded, but not otherwise validated. The rules that apply when a
    #: password is *chosen* have no business here: tightening them later would
    #: lock out accounts whose existing password no longer passes, and any
    #: message explaining that would describe a stored credential to whoever
    #: asked. The only answer this endpoint ever gives is yes or no.
    #:
    #: ``SecretStr`` keeps the value out of tracebacks and any log line that
    #: reprs the request model.
    password: SecretStr = Field(min_length=1, max_length=256)


class MagicLinkExchange(BaseModel):
    """Trade a link token for a session."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=256)


class TenantMembershipView(BaseModel):
    """One organization the caller belongs to."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    tenant_id: uuid.UUID
    name: str
    role: MembershipRole


class SessionView(BaseModel):
    """Who the caller is, and where they can act.

    Returned by the exchange and by ``GET /auth/me``, so the dashboard has one
    shape to render regardless of how it arrived.

    ``token`` is populated **only** on the exchange response, and only for
    non-browser clients — the browser receives the session in an HttpOnly
    cookie, which is the point of the cookie.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    is_platform_admin: bool = False
    active_tenant_id: uuid.UUID | None = None
    memberships: list[TenantMembershipView] = Field(default_factory=list)
    #: Present on exchange only. Never persisted anywhere in plain form.
    token: str | None = None
    #: Whether the account can sign in with a password, so Settings can offer
    #: "set a password" rather than "change password". Never the hash.
    has_password: bool = False
    #: True when a platform operator is acting as this user. The dashboard shows
    #: a banner; the audit log records it independently.
    impersonated: bool = False


class AccountUpdate(BaseModel):
    """What a signed-in user may change about themselves. Not their email:
    changing the address that receives sign-in links needs a verified handover,
    which is its own flow."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=200)


class PasswordChange(BaseModel):
    """Set or change the signed-in user's password.

    ``current_password`` is required whenever the account already has one, and
    is absent for an account that has only ever signed in by link: following the
    link already proved control of the inbox, which is exactly what a reset
    would ask for.
    """

    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr | None = Field(default=None, max_length=256)
    #: The bounds that apply when a password is *chosen* — the same ones signup
    #: enforces (:mod:`app.services.passwords`).
    new_password: SecretStr = Field(min_length=8, max_length=128)


class AccountRegistration(BaseModel):
    """Create an account: the first step, before any business exists."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    #: The bounds that apply when a password is chosen (app.services.passwords).
    password: SecretStr = Field(min_length=8, max_length=128)
