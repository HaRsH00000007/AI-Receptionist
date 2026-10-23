"""Website-based setup: start a crawl, review what it found, then confirm.

The shape of the flow is

    POST /website-setup            start the crawl (a job; returns immediately)
    GET  /website-setup            poll it, then read the draft to review
    PUT  /website-setup/knowledge  save edits to knowledge, questions, messages
    PUT  /website-setup/choices    voice, greeting, area code, chosen number
    POST /website-setup/retry      try a failed crawl again
    POST /website-setup/confirm    create the business — the only step that provisions
    DELETE /website-setup          throw the draft away

Everything is scoped to the signed-in **user**, because none of it has a tenant
yet: the row is found by ``principal.user.id`` and never by an id from the
request, so there is nothing here to tamper with.

**Nothing is bought until ``confirm``.** Starting a crawl costs a crawl and one
LLM call. Confirm hands the reviewed draft to the existing
:class:`app.services.signup.SignupService`, and the number is purchased by the
existing provisioning step behind the existing billing gate — this feature adds
no second path to spending money. A second confirm returns the business the
first one made rather than making another.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from app.api.auth_deps import PrincipalDep
from app.api.deps import SettingsDep, TemporalClientDep
from app.api.v1.signups import _start_workflow
from app.core.config import Settings
from app.core.correlation import get_correlation_id, new_correlation_id
from app.core.errors import AuthorizationError, ConflictError, InvalidInputError, NotFoundError
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models import BusinessProfile, Tenant
from app.models.enums import (
    AuditAction,
    BusinessType,
    GreetingStyle,
    TenantStatus,
    WebsiteOnboardingStatus,
)
from app.models.website_onboarding import WebsiteOnboarding
from app.schemas.signup import OnboardingRequest, SignupResponse
from app.schemas.website import (
    ChoicesUpdate,
    ConfirmRequest,
    CrawlSummaryView,
    KnowledgeUpdate,
    VoiceOptionView,
    WebsiteSetupStart,
    WebsiteSetupView,
)
from app.services.audit import AuditService
from app.services.signup import SignupService
from app.services.status_tokens import issue_status_token
from app.services.website_onboarding import BUILTIN_QUESTIONS
from app.website import guard

logger = get_logger(__name__)

router = APIRouter(prefix="/website-setup", tags=["website setup"])

#: The one language the call path sends. ``app.providers.real.voice`` hardcodes
#: ``"language": "en"`` in the agent body, so offering another would be a lie
#: told in a dropdown. When the adapter learns to send it, this grows.
SUPPORTED_LANGUAGES = ("en",)


async def _job(session: SessionDep, user_id: uuid.UUID) -> WebsiteOnboarding | None:
    return (
        await session.execute(select(WebsiteOnboarding).where(WebsiteOnboarding.user_id == user_id))
    ).scalar_one_or_none()


async def _require_job(session: SessionDep, user_id: uuid.UUID) -> WebsiteOnboarding:
    job = await _job(session, user_id)
    if job is None:
        raise NotFoundError("no website setup in progress")
    return job


async def _saved_view(session: SessionDep, job: WebsiteOnboarding) -> WebsiteSetupView:
    """Flush a write, then view it.

    ``updated_at`` carries a server-side ``onupdate``, so a flush expires it and
    simply reading the attribute would try to reload it — IO outside the async
    greenlet, which is a 500 rather than a slow response. Refreshing the two
    timestamps explicitly is the cheap, visible fix.
    """
    await session.flush()
    await session.refresh(job, ["created_at", "updated_at"])
    return _view(job)


async def _still_live(session: SessionDep, tenant_id: uuid.UUID | None) -> bool:
    """Whether the business a draft created is still a going concern.

    A cancelled or abandoned business does not hold its draft hostage: the
    customer is entitled to start again.
    """
    if tenant_id is None:
        return False
    tenant = await session.get(Tenant, tenant_id)
    return tenant is not None and tenant.status in (TenantStatus.ACTIVE, TenantStatus.PENDING)


def _view(job: WebsiteOnboarding) -> WebsiteSetupView:
    crawl = job.crawl_json or None
    return WebsiteSetupView(
        id=job.id,
        status=job.status,
        website_url=job.website_url,
        attempt=job.attempt,
        last_error=job.last_error,
        ready=job.status in (WebsiteOnboardingStatus.READY, WebsiteOnboardingStatus.CONFIRMED),
        tenant_id=job.tenant_id,
        crawl=CrawlSummaryView.model_validate(crawl) if crawl else None,
        knowledge=job.knowledge_json,
        choices=job.choices_json,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=WebsiteSetupView,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Read a business website and draft a receptionist from it",
)
async def start(
    payload: WebsiteSetupStart,
    principal: PrincipalDep,
    request: Request,
    session: SessionDep,
) -> WebsiteSetupView:
    """Validate the URL, then queue the work.

    Returns 202 immediately: a crawl plus an LLM call is far too slow for a
    request, and the customer is allowed to close the tab. The URL is checked
    here so an unusable address is a form error rather than a job that fails a
    minute later.
    """
    if principal.is_impersonating:
        raise AuthorizationError("a receptionist cannot be set up while impersonating")
    await request.app.state.signup_rate_limiter.check(f"website-setup|{principal.user.id}")

    # Raises InvalidInputError (422) for anything that is not a public website.
    site = guard.check(payload.website_url)

    job = await _job(session, principal.user.id)
    if job is not None and job.status is WebsiteOnboardingStatus.CONFIRMED:
        # Refused only while the business it made is still live — otherwise a
        # customer who deleted their receptionist could never set another one
        # up from a website, because one account holds one draft. Deleting a
        # receptionist clears its draft, so this is the belt to that braces:
        # it also recovers accounts whose draft was left behind.
        if await _still_live(session, job.tenant_id):
            raise ConflictError(
                "this setup has already created a business",
                details={"tenant_id": str(job.tenant_id)},
            )
        job.tenant_id = None

    basics = {
        "full_name": payload.full_name or (principal.user.full_name or ""),
        "notification_email": str(payload.notification_email or principal.user.email),
        "contact_phone": payload.contact_phone,
        "heard_about": payload.heard_about,
    }

    if job is None:
        job = WebsiteOnboarding(user_id=principal.user.id, website_url=site.url)
        session.add(job)
    # Starting again replaces the old attempt wholesale: a half-read crawl of a
    # different website is not something to merge into.
    job.website_url = site.url
    job.status = WebsiteOnboardingStatus.PENDING
    job.attempt = 0
    job.next_attempt_at = None
    job.last_error = None
    job.finished_at = None
    job.crawl_json = None
    job.knowledge_json = None
    job.choices_json = basics
    view = await _saved_view(session, job)

    logger.info(
        "website setup queued",
        extra={"job_id": str(job.id), "host": site.host, "user_id": str(principal.user.id)},
    )
    return view


@router.get("", response_model=WebsiteSetupView, summary="The website setup in progress")
async def read(principal: PrincipalDep, session: SessionDep) -> WebsiteSetupView:
    return _view(await _require_job(session, principal.user.id))


@router.post("/retry", response_model=WebsiteSetupView, summary="Try a failed website read again")
async def retry(principal: PrincipalDep, request: Request, session: SessionDep) -> WebsiteSetupView:
    job = await _require_job(session, principal.user.id)
    if job.status is not WebsiteOnboardingStatus.FAILED:
        raise ConflictError(
            "this setup is not waiting to be retried",
            details={"status": job.status.value},
        )
    await request.app.state.signup_rate_limiter.check(f"website-setup|{principal.user.id}")

    # Re-checked, because the address may have been fine and the site may not
    # have been — or DNS may since have started answering with a private one.
    guard.check(job.website_url)
    job.status = WebsiteOnboardingStatus.PENDING
    job.attempt = 0
    job.next_attempt_at = None
    job.last_error = None
    return await _saved_view(session, job)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Discard the website setup")
async def discard(principal: PrincipalDep, session: SessionDep) -> None:
    job = await _job(session, principal.user.id)
    if job is None:
        return
    if job.status is WebsiteOnboardingStatus.CONFIRMED:
        # The draft is the record of how a live business was built; deleting it
        # would erase that. Nothing is lost by keeping it.
        raise ConflictError("this setup has already created a business")
    await session.delete(job)


# ---------------------------------------------------------------------------
# Reviewing and editing
# ---------------------------------------------------------------------------


def _knowledge_document(payload: KnowledgeUpdate) -> dict[str, Any]:
    """The approved draft, as it is stored.

    Whole-draft replacement, so a removed item is actually gone rather than
    lingering with ``enabled: false``. The built-in questions are re-keyed from
    our own list: a customer may switch "Email" off, but the key the agent's
    instructions use is not theirs to rename.
    """
    builtin = {question["key"]: question for question in BUILTIN_QUESTIONS}
    questions: list[dict[str, Any]] = []
    for entry in payload.questions:
        if entry.key in builtin:
            questions.append({**builtin[entry.key], "enabled": entry.enabled})
        else:
            questions.append(
                {
                    "key": entry.key,
                    "label": entry.label,
                    "prompt": entry.prompt,
                    "builtin": False,
                    "enabled": entry.enabled,
                }
            )
    return {
        "business_name": payload.business_name,
        "business_summary": payload.business_summary,
        "operating_hours": payload.operating_hours,
        "services": payload.services,
        "items": [item.model_dump() for item in payload.items],
        "questions": questions,
        "sms_templates": [template.model_dump() for template in payload.sms_templates],
    }


def _merged_choices(
    payload: ChoicesUpdate, settings: Settings, existing: dict[str, Any] | None
) -> dict[str, Any]:
    """Validate the last few screens and fold them into what is already there."""
    if payload.language not in SUPPORTED_LANGUAGES:
        # Refused rather than stored-and-ignored: a saved "fr" that the call
        # path never sends would be a promise the product does not keep.
        raise InvalidInputError(
            "English is the only language the receptionist speaks today.",
            details={"field": "language"},
        )
    if payload.voice_id and payload.voice_id not in _voice_ids(settings):
        raise InvalidInputError("That voice isn't available.", details={"field": "voice_id"})

    merged = dict(existing or {})
    update = payload.model_dump(exclude_none=True)
    # Blank strings mean "not answered on this screen yet", not "clear it".
    merged.update({key: value for key, value in update.items() if value != ""})
    return merged


@router.put(
    "/knowledge",
    response_model=WebsiteSetupView,
    summary="Save edits to the knowledge, questions and messages",
)
async def save_knowledge(
    payload: KnowledgeUpdate, principal: PrincipalDep, session: SessionDep
) -> WebsiteSetupView:
    """Replace the draft with what the customer approved.

    Whole-draft replacement, so a removed item is actually gone rather than
    lingering with ``enabled: false``. The built-in questions are re-keyed from
    our own list: a customer may switch "Email" off, but the key the agent's
    instructions use is not theirs to rename.
    """
    job = await _require_job(session, principal.user.id)
    _require_editable(job)
    job.knowledge_json = _knowledge_document(payload)
    return await _saved_view(session, job)


@router.put(
    "/choices", response_model=WebsiteSetupView, summary="Save voice, greeting and number choices"
)
async def save_choices(
    payload: ChoicesUpdate, principal: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> WebsiteSetupView:
    job = await _require_job(session, principal.user.id)
    _require_editable(job)
    job.choices_json = _merged_choices(payload, settings, job.choices_json)
    return await _saved_view(session, job)


@router.get(
    "/voices",
    response_model=list[VoiceOptionView],
    summary="The voices this deployment can use",
)
async def voices(principal: PrincipalDep, settings: SettingsDep) -> list[VoiceOptionView]:
    """Exactly the configured voices, with no invented extras.

    ``ELEVENLABS_VOICE_MAP`` is the source of truth: a voice that is not in it
    has never been checked against this account and would fail on publish.
    """
    del principal  # authorization only
    options: list[VoiceOptionView] = []
    for style, voice_id in settings.voice_map.items():
        options.append(
            VoiceOptionView(voice_id=voice_id, label=style.replace("_", " ").title(), style=style)
        )
    default = settings.elevenlabs_default_voice_id
    if default and default not in {option.voice_id for option in options}:
        options.append(VoiceOptionView(voice_id=default, label="Default", style=None))
    return options


def _voice_ids(settings: Settings) -> set[str]:
    return {*settings.voice_map.values(), settings.elevenlabs_default_voice_id}


def _require_editable(job: WebsiteOnboarding) -> None:
    if job.status is WebsiteOnboardingStatus.CONFIRMED:
        raise ConflictError("this setup has already created a business")
    if job.status is not WebsiteOnboardingStatus.READY:
        raise ConflictError("there is nothing to review yet", details={"status": job.status.value})


# ---------------------------------------------------------------------------
# Confirming — the only step that creates anything
# ---------------------------------------------------------------------------


@router.post(
    "/confirm",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the receptionist from the reviewed draft",
)
async def confirm(
    principal: PrincipalDep,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    temporal: TemporalClientDep,
    payload: ConfirmRequest | None = None,
) -> SignupResponse:
    """Hand the approved draft to the existing signup pipeline.

    No new provisioning path: this builds the same ``OnboardingRequest`` the
    manual form submits, so the number is bought by the same step, behind the
    same billing gate, with the same idempotency guards. What the website added
    is carried on the business profile, where the agent is rebuilt from.

    The wizard posts every screen here in one request. Keeping the review,
    questions, voice and greeting in the browser until this moment is what makes
    an abandoned setup leave nothing behind: press Back and the only thing that
    was ever written is the crawl, which the discard removes. The body is
    optional, so a client that saved as it went still works, and so does a
    repeat of a request that has already created a business.

    Idempotent: a job already marked confirmed returns its business with 200.
    """
    if principal.is_impersonating:
        raise AuthorizationError("a business cannot be created while impersonating")

    job = await _require_job(session, principal.user.id)
    if job.status is WebsiteOnboardingStatus.CONFIRMED and job.tenant_id is not None:
        return await _existing(session, settings, job, response)
    if job.status is not WebsiteOnboardingStatus.READY:
        raise ConflictError(
            "review the details before creating your receptionist",
            details={"status": job.status.value},
        )
    await request.app.state.signup_rate_limiter.check(f"website-confirm|{principal.user.id}")

    # Applied before anything is built, and through the same helpers the save
    # routes use, so "what the customer approved" cannot mean one thing when it
    # is saved step by step and another when it arrives all at once.
    if payload is not None and payload.knowledge is not None:
        job.knowledge_json = _knowledge_document(payload.knowledge)
    if payload is not None and payload.choices is not None:
        job.choices_json = _merged_choices(payload.choices, settings, job.choices_json)

    knowledge = dict(job.knowledge_json or {})
    choices = dict(job.choices_json or {})
    # A separate name, not a reassignment of ``payload``: that one is the
    # customer's last screenful, and overwriting it with the signup request
    # built from it would leave two different shapes behind one variable.
    signup = _onboarding_request(knowledge, choices, fallback_email=principal.user.email)

    correlation_id = get_correlation_id() or new_correlation_id()
    result = await SignupService(session, settings).submit(
        signup, correlation_id=correlation_id, owner=principal.user
    )

    # The website's contribution, onto the thing the agent is generated from.
    # Queried rather than reached through ``tenant.business_profile``: that is a
    # lazy relationship, and touching it here would try to emit IO outside the
    # async greenlet and blow up mid-signup.
    profile = (
        await session.execute(
            select(BusinessProfile).where(BusinessProfile.tenant_id == result.tenant.id)
        )
    ).scalar_one_or_none()
    if profile is not None and result.created:
        profile.knowledge_json = [
            item for item in knowledge.get("items", []) if item.get("enabled", True)
        ]
        profile.caller_questions_json = [
            question for question in knowledge.get("questions", []) if question.get("enabled", True)
        ]
        profile.sms_templates_json = knowledge.get("sms_templates", [])
        voice_id = str(choices.get("voice_id") or "")
        if voice_id in _voice_ids(settings):
            profile.voice_id = voice_id

    job.tenant_id = result.tenant.id
    job.status = WebsiteOnboardingStatus.CONFIRMED
    job.finished_at = datetime.now(UTC)

    principal.session.active_tenant_id = result.tenant.id
    if result.created:
        AuditService(session).record(
            AuditAction.BUSINESS_CREATED,
            actor_type=principal.actor_type,
            actor=principal.user,
            tenant_id=result.tenant.id,
            entity_type="tenant",
            entity_id=result.tenant.id,
            meta={
                "business_type": result.tenant.business_type.value,
                "source": "website",
                "website_url": job.website_url,
                "knowledge_items": len(profile.knowledge_json or []) if profile else 0,
            },
        )

    if settings.uses_temporal:
        # Committed first: the workflow's activities read this run.
        await session.commit()
        await _start_workflow(temporal, settings, result)

    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    logger.info(
        "business created from a website",
        extra={
            "tenant_id": str(result.tenant.id),
            "business_created": result.created,
            "pages_read": (job.crawl_json or {}).get("pages_read", 0),
        },
    )
    return _response_for(result, settings)


def _onboarding_request(
    knowledge: dict[str, Any], choices: dict[str, Any], *, fallback_email: str
) -> OnboardingRequest:
    """Build the manual form's own request object out of the draft.

    Deliberately goes through ``OnboardingRequest`` rather than writing rows
    directly: every validator the manual flow relies on — a real email, a
    plausible phone number, a US number from the offered list, a plan that is
    actually sold — applies to this path too, and a draft that cannot satisfy
    them is a 422 the customer can fix on the review screen.
    """
    services = [entry for entry in knowledge.get("services", []) if str(entry).strip()]
    enabled_items = [item for item in knowledge.get("items", []) if item.get("enabled", True)]
    if not services:
        # The form requires at least one service. A website that never listed
        # any still has a summary, so fall back to the business description
        # rather than refusing a setup the customer has just reviewed.
        services = [str(knowledge.get("business_summary") or "General enquiries")[:80]]

    escalation = str(choices.get("escalation_rules") or "")
    hours = str(knowledge.get("operating_hours") or "").strip() or "Not published"

    business_type = str(choices.get("business_type") or BusinessType.OTHER.value)
    greeting_style = str(choices.get("greeting_style") or GreetingStyle.PROFESSIONAL.value)

    try:
        return OnboardingRequest(
            business_name=str(knowledge.get("business_name") or "").strip()
            or str(choices.get("full_name") or "My business"),
            business_type=BusinessType(business_type),
            services=services,
            operating_hours=hours,
            greeting_style=GreetingStyle(greeting_style),
            custom_greeting=str(choices.get("custom_greeting") or ""),
            escalation_rules=escalation,
            notification_email=str(choices.get("notification_email") or fallback_email),
            area_code=str(choices.get("area_code") or "").strip() or "805",
            selected_number=str(choices.get("selected_number") or ""),
            plan=str(choices.get("plan") or "starter"),
            contact_phone=str(choices.get("contact_phone") or ""),
        )
    except ValueError as exc:
        # Pydantic's message names the field, which is what the wizard needs to
        # send the customer back to the right screen.
        raise InvalidInputError(
            "Some details are still missing. Check the review screen and try again.",
            details={"detail": str(exc)[:300], "items": len(enabled_items)},
        ) from exc


async def _existing(
    session: SessionDep, settings: Settings, job: WebsiteOnboarding, response: Response
) -> SignupResponse:
    """A second confirm. Returns the business the first one made."""
    from app.models import ProvisioningRun, Tenant

    tenant = await session.get(Tenant, job.tenant_id)
    if tenant is None:  # pragma: no cover - the FK is ON DELETE SET NULL
        raise NotFoundError("the business from this setup no longer exists")
    run = (
        await session.execute(
            select(ProvisioningRun)
            .where(ProvisioningRun.tenant_id == tenant.id)
            .order_by(ProvisioningRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None:  # pragma: no cover
        raise NotFoundError("no provisioning run for this business")

    response.status_code = status.HTTP_200_OK
    return SignupResponse(
        tenant_id=tenant.id,
        run_id=run.id,
        tenant_status=tenant.status,
        provisioning_status=run.status,
        correlation_id=run.correlation_id,
        business_name=tenant.name,
        contact_email=tenant.contact_email,
        timezone=tenant.timezone,
        created=False,
        status_token=issue_status_token(settings, tenant.id),
        signed_in=True,
    )


def _response_for(result: Any, settings: Settings) -> SignupResponse:
    return SignupResponse(
        tenant_id=result.tenant.id,
        run_id=result.run.id,
        tenant_status=result.tenant.status,
        provisioning_status=result.run.status,
        correlation_id=result.run.correlation_id,
        business_name=result.tenant.name,
        contact_email=result.tenant.contact_email,
        timezone=result.tenant.timezone,
        created=result.created,
        status_token=issue_status_token(settings, result.tenant.id),
        signed_in=True,
    )
