"""Request and response contracts for authentication.

DTOs, not models. Nothing here exposes a database row directly — in particular
nothing exposes ``token_hash``, ``is_platform_admin`` on another user, or any
internal id a client has no use for.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
    #: True when a platform operator is acting as this user. The dashboard shows
    #: a banner; the audit log records it independently.
    impersonated: bool = False
