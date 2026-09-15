"""The production call path.

Two endpoints, both on the *audible* path, which makes them unlike every other
route in this application: a slow response here is a caller listening to
silence, and an exception is a dropped call. So both follow the same rules:

* **Never raise.** Every failure produces valid TwiML. The global exception
  handler returns JSON, which Twilio cannot read — it would play nothing.
* **Never wait.** Redis first with a millisecond budget, PostgreSQL behind it,
  and a safe default behind that.
* **Never trust the request.** Twilio's signature is verified before the dialled
  number is used to look anything up, because that number decides which tenant's
  agent answers. An unsigned request that could choose a tenant is cross-tenant
  routing handed to anyone who can guess a URL.

The POC path (``CALL_PATH=elevenlabs_native``) is untouched by all of this: when
ElevenLabs owns the number, Twilio never posts here at all.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CacheDep, CircuitsDep, MetricsDep, SettingsDep
from app.cache.lookup import ConfigCache, NumberDirectory
from app.core.config import Settings
from app.core.correlation import get_correlation_id
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.models import Agent, Tenant
from app.models.enums import AgentStatus
from app.services.normalization import normalize_phone
from app.services.signatures import public_request_url, verify_twilio
from app.telephony import twiml
from app.telephony.routing import (
    CallDisposition,
    RoutingDecision,
    RoutingInput,
    decide,
    vendor_outage_decision,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

#: TwiML is XML, and Twilio ignores a response that does not say so.
_TWIML_MEDIA_TYPE = "application/xml"


def _twiml_response(document: str, *, status_code: int = 200) -> Response:
    return Response(content=document, media_type=_TWIML_MEDIA_TYPE, status_code=status_code)


@router.post("/inbound", summary="Twilio inbound call webhook")
async def inbound_call(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    cache: CacheDep,
    metrics: MetricsDep,
    circuits: CircuitsDep,
) -> Response:
    """Decide what happens to an inbound call, and answer in TwiML.

    Returns 200 with TwiML in every case, including refusal. A 4xx or 5xx makes
    Twilio play its own generic failure message to the caller and retry, which
    is both a worse experience and a duplicate call.
    """
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}

    if not _signature_ok(request, params, settings):
        # Refuse *before* the dialled number is used to select a tenant. This
        # check is what stops an unsigned request choosing whose agent answers.
        logger.warning(
            "rejected an unsigned inbound call webhook",
            extra={"correlation_id": get_correlation_id()},
        )
        metrics.counter("inbound_calls_total").inc(disposition="unsigned")
        return _twiml_response(twiml.reject(reason="rejected"), status_code=403)

    dialled = _normalized(params.get("To"))
    caller = _normalized(params.get("From"))
    call_sid = params.get("CallSid", "")

    routing = await _resolve(session, cache, settings, dialled)

    # The vendor outage path. When ElevenLabs has been failing consistently,
    # connecting the media stream would leave the caller in silence until the
    # websocket handshake times out. Answering with a recorded apology and a
    # voicemail is a degraded service; silence is a lost customer, and the
    # business never learns the call happened.
    #
    # Checked here rather than caught as an exception below, because by the time
    # <Connect><Stream> is in Twilio's hands the call has left us — there is no
    # later moment at which we could still choose to take a message.
    voice_circuit = circuits.for_vendor("elevenlabs")
    if routing.tenant_id is not None and not voice_circuit.allows():
        decision = vendor_outage_decision(routing)
    else:
        decision = decide(routing)

    logger.info(
        "inbound call routed",
        extra={
            # The caller's number is PII and is deliberately absent. The call
            # sid is enough to correlate with Twilio's own record, and the call
            # row carries the number under the retention policy that governs it.
            "call_sid": call_sid,
            "tenant_id": str(decision.tenant_id) if decision.tenant_id else None,
            "disposition": decision.disposition.value,
            "reason": decision.reason,
            "has_caller_id": bool(caller),
        },
    )

    # Disposition only — never a tenant id. An unbounded label set kills the
    # scraper; identifiers belong in the log line above.
    metrics.counter("inbound_calls_total").inc(
        disposition=decision.disposition.value, reason=decision.reason
    )
    return _twiml_response(_render(decision, settings, call_sid=call_sid))


@router.post("/init", summary="ElevenLabs conversation initiation webhook")
async def conversation_init(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    cache: CacheDep,
    metrics: MetricsDep,
) -> dict[str, Any]:
    """Hand the agent this tenant's dynamic variables.

    Called by ElevenLabs as a conversation starts, so it sits inside the silence
    before the caller hears anything — the hard 300ms budget in the production
    plan is this endpoint's.

    A shared agent is a generic receptionist until this answers; the variables
    are what make it *this* business's receptionist. Returning the wrong
    tenant's variables would have a salon's agent answer a law firm's call, so
    the lookup is by dialled number and nothing in the request can override it.
    """
    # The only latency a caller can hear. Timed including the failure paths,
    # because "it took 4 seconds and then served a generic config" is the
    # symptom that matters, and an untimed error path hides it.
    with metrics.histogram("voice_init_duration_seconds").time():
        payload: dict[str, Any] = {}
        try:
            payload = await request.json()
        except ValueError:
            logger.warning("conversation init payload was not valid JSON")

        dialled = _normalized(_dialled_number_from(payload))

        routing = await _resolve(session, cache, settings, dialled)
        if routing.tenant_id is None:
            # A generic, safe configuration rather than an error. The
            # conversation has already started; failing here is silence on a
            # live call.
            logger.warning("conversation init could not resolve a tenant")
            metrics.counter("voice_init_total").inc(outcome="generic_fallback")
            return {"dynamic_variables": _generic_variables(settings)}

        variables = await _dynamic_variables(session, cache, settings, routing)
        metrics.counter("voice_init_total").inc(outcome="resolved")
        return {"dynamic_variables": variables}


# ===========================================================================
# Resolution
# ===========================================================================


async def _resolve(
    session: AsyncSession,
    cache: CacheDep,
    settings: Settings,
    dialled: str | None,
) -> RoutingInput:
    """Who owns this number, and which agent serves it.

    Redis, then PostgreSQL, and a miss is simply "no tenant" — never an
    exception. Two indexed reads in the worst case.
    """
    if not dialled:
        return RoutingInput(None, None, None, None, None)

    owner = await NumberDirectory(cache, settings).resolve(session, dialled)
    if owner is None:
        return RoutingInput(dialled, None, None, None, None)

    row = (
        await session.execute(
            select(Tenant.name, Agent.elevenlabs_agent_id)
            .select_from(Tenant)
            .outerjoin(
                Agent,
                (Agent.tenant_id == Tenant.id) & (Agent.status == AgentStatus.ACTIVE),
            )
            .where(Tenant.id == owner.tenant_id)
            .limit(1)
        )
    ).first()

    business_name, agent_id = row if row is not None else (None, None)
    return RoutingInput(
        dialled_e164=owner.e164,
        tenant_id=owner.tenant_id,
        tenant_status=owner.tenant_status,
        agent_id=agent_id,
        business_name=business_name,
    )


async def _dynamic_variables(
    session: AsyncSession,
    cache: CacheDep,
    settings: Settings,
    routing: RoutingInput,
) -> dict[str, str]:
    """The per-call configuration a shared agent needs.

    This is what turns one generic vertical agent into *this* business's
    receptionist — the mechanism M7 depends on. Served from the version-keyed
    config cache, so a tenant who publishes a new prompt is answered with it on
    the next call without a vendor round trip.

    Strings only, and only what the conversation needs. The payload crosses into
    a vendor's request log, so it carries nothing secret and none of our
    internal identifiers beyond the tenant id the vendor already sees.
    """
    assert routing.tenant_id is not None

    variables = _generic_variables(settings)
    variables["business_name"] = routing.business_name or variables["business_name"]
    variables["tenant_id"] = str(routing.tenant_id)

    config = await ConfigCache(cache, settings).get(session, routing.tenant_id)
    if config is None:
        # A tenant with no live config still gets a working receptionist. The
        # generic variables above already describe one.
        return variables

    variables["config_version"] = str(config.get("version", ""))
    first_message = config.get("first_message")
    if isinstance(first_message, str) and first_message:
        variables["greeting"] = first_message
    system_prompt = config.get("system_prompt")
    if isinstance(system_prompt, str) and system_prompt:
        variables["business_instructions"] = system_prompt
    return variables


def _generic_variables(settings: Settings) -> dict[str, str]:
    """The safe default.

    Used when nothing could be resolved. A generic but competent receptionist is
    a far better outcome than a failed conversation: the caller is still heard,
    a message is still taken, and the business still finds out.
    """
    variables = {
        "business_name": "this business",
        "greeting": "Thank you for calling. How can I help you today?",
        "fallback_behavior": "Take a message with the caller's name and number.",
    }
    if settings.recording_consent_announcement:
        # Injected rather than left to the prompt. Consent is a legal
        # requirement in some jurisdictions, and "the model usually remembers"
        # is not a compliance position.
        variables["recording_notice"] = "This call may be recorded for quality purposes."
    return variables


# ===========================================================================
# Rendering
# ===========================================================================


def _render(decision: RoutingDecision, settings: Settings, *, call_sid: str) -> str:
    match decision.disposition:
        case CallDisposition.CONNECT_AGENT:
            assert decision.agent_id is not None
            return twiml.connect_stream(
                websocket_url=f"{settings.elevenlabs_stream_url}?agent_id={decision.agent_id}",
                parameters={
                    # Identifiers only. Configuration is fetched by /voice/init,
                    # which keeps prompts out of Twilio's logs and out of a URL.
                    "tenant_id": str(decision.tenant_id or ""),
                    "call_sid": call_sid,
                },
            )
        case CallDisposition.VOICEMAIL:
            return twiml.say_and_record(
                message=decision.message,
                max_length_s=settings.voicemail_max_length_s,
                voice=settings.twilio_fallback_voice,
            )
        case CallDisposition.UNAVAILABLE:
            return twiml.say_and_hangup(
                message=decision.message, voice=settings.twilio_fallback_voice
            )
        case CallDisposition.REJECT:
            return twiml.reject()


# ===========================================================================
# Helpers
# ===========================================================================


def _signature_ok(request: Request, params: dict[str, str], settings: Settings) -> bool:
    """Whether this inbound call may be acted on.

    Stricter than the status callback, deliberately. A status callback only
    records what already happened; *this* request decides whose agent answers a
    live call, so accepting an unverified one is cross-tenant routing handed to
    anyone who can reach the URL.

    The rule is therefore "verify whenever verification is possible": if an auth
    token is configured we can check the signature, so we must, in every
    environment. Only a deployment with no Twilio credential at all — a purely
    local loop with no Twilio account — falls back to the environment policy,
    and even then a production-like environment still refuses.
    """
    auth_token = settings.twilio_auth_token.get_secret_value()
    valid = verify_twilio(
        url=public_request_url(
            observed_url=str(request.url),
            path=request.url.path,
            query=request.url.query,
            public_base=settings.public_api_url,
        ),
        params=params,
        header=request.headers.get("x-twilio-signature", ""),
        auth_token=auth_token,
    )
    if valid:
        return True
    if auth_token:
        return False
    return not settings.webhooks_require_signature


def _normalized(value: Any) -> str | None:
    """Normalize to E.164, treating anything unparseable as absent.

    Never raises: this runs on a PSTN-supplied value, and a malformed caller id
    must not be able to fail a call.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return normalize_phone(value)
    except Exception:  # noqa: BLE001 - a bad number is "no number", not a crash
        return None


def _dialled_number_from(payload: dict[str, Any]) -> str | None:
    """Find the number that was dialled, whatever shape the vendor sent.

    ElevenLabs has moved this field between payload shapes, and a call-path
    endpoint is the wrong place to discover that: a miss here means a shared
    agent answers as a generic receptionist instead of as the business. So
    several known locations are tried rather than one assumed.
    """
    candidates: list[Any] = [
        payload.get("agent_number"),
        payload.get("called_number"),
        payload.get("to"),
    ]
    for nested_key in ("call", "call_info", "metadata", "phone_call"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            candidates.extend([nested.get("to"), nested.get("agent_number")])

    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None
