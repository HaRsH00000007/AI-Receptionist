"""Every receptionist this account has, as a gallery.

    GET /me/receptionists

Account-scoped rather than tenant-scoped, which makes it the one route in the
portal that deliberately reads across tenants — so the filtering is the whole
security story: the membership table is the join, and a tenant with no accepted
membership for this user cannot appear no matter what is passed.

It exists because the dashboard is otherwise per-business. An owner with four
salons had to switch workspace four times to answer "which of my receptionists
is live and taking calls?", and the switcher could only ever show them a name.

Two queries, not one per card. The join below collects the standing facts, and
one grouped count collects recent activity; doing it per membership would be an
N+1 on the first screen an owner sees.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_deps import PrincipalDep
from app.api.deps import CacheDep, ProvidersDep, SettingsDep
from app.db.session import SessionDep
from app.models import Agent, BusinessProfile, Call, Membership, PhoneNumber, Tenant
from app.models.enums import AgentStatus, MembershipRole, PhoneNumberStatus
from app.schemas.views import ReceptionistCardView
from app.schemas.website import VoiceOptionView
from app.services.voice_catalogue import VoiceCatalogue

router = APIRouter(prefix="/me", tags=["receptionists"])

#: How far back "recent calls" reaches. A month, because that is the window the
#: rest of the product bills and reports on.
ACTIVITY_DAYS = 30


@router.get(
    "/receptionists",
    response_model=list[ReceptionistCardView],
    summary="Every receptionist this account has",
)
async def list_receptionists(
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    cache: CacheDep,
) -> list[ReceptionistCardView]:
    """The gallery, newest business last.

    Cancelled and abandoned businesses are included rather than hidden: the
    account still owns their history, and a gallery that silently dropped one
    would leave an owner wondering where it went. Their card says what state it
    is in and offers nothing that would act on it.
    """
    rows = (await session.execute(_cards(principal.user.id))).all()
    if not rows:
        return []

    tenant_ids = [tenant.id for tenant, *_ in rows]
    calls = await _recent_calls(session, tenant_ids)
    # One catalogue read for the whole gallery, so a voice is named rather than
    # shown as an opaque id. Cached, and a vendor that is down simply leaves
    # the names off.
    voices = {
        option.voice_id: option
        for option in await VoiceCatalogue(settings, providers.elevenlabs, cache).options()
    }

    return [
        ReceptionistCardView(
            tenant_id=tenant.id,
            name=tenant.name,
            status=tenant.status,
            role=role,
            agent_name=profile.agent_name if profile else None,
            # The face follows the voice, so the gallery and the voice picker
            # cannot disagree about who this receptionist is. Empty when no
            # voice was chosen; the client derives one from the tenant id.
            avatar=_avatar(voices, profile),
            # Null for a receptionist whose own voice was never chosen; the
            # greeting style's default is resolved at publish time and naming it
            # here would mean guessing at it twice.
            voice=_voice(voices, profile),
            phone_e164=phone_e164,
            live=agent_id is not None,
            calls_recent=calls.get(tenant.id, 0),
            activity_days=ACTIVITY_DAYS,
            created_at=tenant.created_at,
        )
        for tenant, role, profile, phone_e164, agent_id in rows
    ]


def _chosen(
    voices: dict[str, VoiceOptionView], profile: BusinessProfile | None
) -> VoiceOptionView | None:
    """The catalogue entry for the voice this business chose, if any.

    ``None`` covers three cases that all mean "nothing to say about the voice":
    no profile yet, no voice of its own — the greeting style's default is
    resolved at publish time, and naming it here would mean guessing at that
    resolution a second time — and a catalogue that could not be read.
    """
    if profile is None or not profile.voice_id:
        return None
    return voices.get(profile.voice_id)


def _voice(voices: dict[str, VoiceOptionView], profile: BusinessProfile | None) -> str | None:
    chosen = _chosen(voices, profile)
    return chosen.label if chosen else None


def _avatar(voices: dict[str, VoiceOptionView], profile: BusinessProfile | None) -> str:
    chosen = _chosen(voices, profile)
    return chosen.avatar if chosen else ""


def _cards(
    user_id: uuid.UUID,
) -> Select[tuple[Tenant, MembershipRole, BusinessProfile, str, uuid.UUID]]:
    # The annotation is what SQLAlchemy infers from the columns, not what the
    # rows actually hold: three of them are outer-joined and come back as None
    # for a business that has no profile, number or agent yet. The reader below
    # treats them as optional, which is the truth.
    """The standing facts behind one gallery, in one statement.

    Outer joins throughout: a business still provisioning has no number and no
    agent yet, and it has to appear — it is the one an owner is most likely to
    be looking for.
    """
    return (
        select(
            Tenant,
            Membership.role,
            BusinessProfile,
            PhoneNumber.e164,
            Agent.id,
        )
        .join(Membership, Membership.tenant_id == Tenant.id)
        .outerjoin(BusinessProfile, BusinessProfile.tenant_id == Tenant.id)
        .outerjoin(
            PhoneNumber,
            (PhoneNumber.tenant_id == Tenant.id) & (PhoneNumber.status == PhoneNumberStatus.ACTIVE),
        )
        .outerjoin(
            Agent,
            (Agent.tenant_id == Tenant.id) & (Agent.status == AgentStatus.ACTIVE),
        )
        .where(Membership.user_id == user_id)
        # An invitation grants nothing until it is accepted, so an unaccepted
        # one must not put somebody else's receptionist in this gallery.
        .where(Membership.accepted_at.is_not(None))
        .order_by(Tenant.created_at)
    )


async def _recent_calls(session: AsyncSession, tenant_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many calls each of these tenants took in the last month.

    Grouped in one statement and keyed by tenant, because the alternative is a
    count per card and this is the first screen an owner sees.
    """
    since = datetime.now(UTC) - timedelta(days=ACTIVITY_DAYS)
    rows = await session.execute(
        select(Call.tenant_id, func.count(Call.id))
        .where(Call.tenant_id.in_(tenant_ids))
        .where(Call.started_at >= since)
        .group_by(Call.tenant_id)
    )
    return dict(rows.all())  # type: ignore[arg-type]
