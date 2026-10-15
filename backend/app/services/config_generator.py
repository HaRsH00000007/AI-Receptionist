"""Turn a business profile into an agent configuration.

The LLM does one job here — read messy free text into a structured shape — and
everything around it is deterministic:

* the prompt is a versioned file, so the same form yields the same request;
* the reply is validated against a Pydantic schema, and a schema failure gets
  exactly one repair attempt;
* a second failure falls back to a template rendered from the raw form, so
  signup never hard-blocks on a vendor being down;
* the system prompt the agent actually runs is rendered by us from the validated
  structure, not taken verbatim from the model.

That last point matters: the model never writes the text that is handed to the
voice vendor unreviewed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import BusinessProfile, Tenant
from app.models.enums import AgentConfigSource, GreetingStyle
from app.prompts.loader import (
    AGENT_CONFIG_PROMPT,
    AGENT_CONFIG_PROMPT_WITH_KNOWLEDGE,
    load_prompt,
    split_sections,
)
from app.providers.protocols import LLMProvider
from app.schemas.agent_config import GeneratedAgentConfig
from app.schemas.business import (
    BusinessHours,
    DaySchedule,
    EscalationPolicy,
    Weekday,
)

logger = get_logger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

#: Greeting style → the opening line's register. Deterministic, so a tenant's
#: greeting cannot change because a model felt different today.
_GREETING_TEMPLATES: dict[GreetingStyle, str] = {
    GreetingStyle.PROFESSIONAL: "Thank you for calling {name}. How can I help you today?",
    GreetingStyle.FRIENDLY: "Hi, thanks for calling {name}! What can I do for you?",
    GreetingStyle.FORMAL: "Good day, you have reached {name}. How may I assist you?",
}


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    config: GeneratedAgentConfig
    source: AgentConfigSource
    #: The model or renderer that produced it, recorded on the config row.
    detail: str
    template_version: str


def extract_json(text: str) -> dict[str, object]:
    """Parse a model reply that may be wrapped in a markdown fence."""
    cleaned = _FENCE.sub("", text.strip()).strip()
    if not cleaned.startswith("{"):
        # Some models prepend a sentence despite instructions; take the object.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in the reply")
        cleaned = cleaned[start : end + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("reply was not a JSON object")
    return parsed


def _template_for(profile: BusinessProfile) -> str:
    """v2 when this business learned from its website, v1 otherwise."""
    return AGENT_CONFIG_PROMPT_WITH_KNOWLEDGE if profile.knowledge_json else AGENT_CONFIG_PROMPT


def render_knowledge(items: list[dict[str, Any]] | None) -> str:
    """Lay approved website knowledge out for the prompt.

    Only enabled items, each with its title and source. A customer who switched
    an item off on the review screen means the receptionist must not say it, so
    "disabled" is enforced here rather than by hoping the model notices.
    """
    if not items:
        return "none"
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        source = str(item.get("source_url") or "").strip()
        block = f"### {title}\n{content}" if title else content
        if source:
            block += f"\n(Source: {source})"
        lines.append(block)
    return "\n\n".join(lines) if lines else "none"


def render_caller_questions(questions: list[dict[str, Any]] | None) -> str:
    """The enabled caller questions, as one line per question."""
    if not questions:
        return "name and phone number only"
    lines = [
        f"- {str(entry.get('label') or '').strip()}: {str(entry.get('prompt') or '').strip()}"
        for entry in questions
        if isinstance(entry, dict) and entry.get("enabled", True) and entry.get("label")
    ]
    return "\n".join(lines) if lines else "name and phone number only"


def enabled_question_labels(questions: list[dict[str, Any]] | None) -> list[str]:
    """Just the labels, for the deterministic half of the system prompt.

    Order is preserved: a business that asks "which service?" before "when
    would you like to come in?" means that order, and reordering the list on
    the review screen is how they say so.
    """
    if not questions:
        return []
    labels: list[str] = []
    for entry in questions:
        if not isinstance(entry, dict) or not entry.get("enabled", True):
            continue
        label = str(entry.get("label") or "").strip()
        if label and label not in labels:
            labels.append(label[:80])
    return labels


class ConfigGenerator:
    """Generates one tenant's agent configuration."""

    def __init__(self, llm: LLMProvider, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    async def generate(self, tenant: Tenant, profile: BusinessProfile) -> GenerationOutcome:
        # A business set up from the form has no website knowledge and keeps
        # using v1 — unchanged prompt, unchanged output. v2 exists only to
        # carry the knowledge block, and the version used is recorded on the
        # config row, so "which prompt wrote this agent" stays answerable.
        template_name = _template_for(profile)
        system, user_template = split_sections(load_prompt(template_name))
        user = self._render_input(tenant, profile, user_template)

        attempts: list[str] = []
        for attempt in range(2):
            message = user if attempt == 0 else self._repair_prompt(user, attempts[-1])
            try:
                reply = await self.llm.complete(
                    system=system,
                    user=message,
                    model=self.settings.llm_config_model,
                    max_tokens=self.settings.llm_max_tokens,
                    temperature=self.settings.llm_temperature,
                )
                config = GeneratedAgentConfig.model_validate(extract_json(reply.text))
            except (ValueError, ValidationError) as exc:
                # A malformed or schema-invalid reply is the model's problem, not
                # a transport problem: repair once, then stop asking.
                attempts.append(str(exc)[:500])
                logger.warning(
                    "llm config reply rejected",
                    extra={
                        "tenant_id": str(tenant.id),
                        "attempt": attempt + 1,
                        "reason": type(exc).__name__,
                    },
                )
                continue
            except AppError as exc:
                # Transport or vendor failure. Fall back rather than fail the
                # run: a working receptionist from a template beats no
                # receptionist at all.
                logger.warning(
                    "llm unavailable for config generation",
                    extra={"tenant_id": str(tenant.id), "code": exc.code},
                )
                break

            return GenerationOutcome(
                config=self._with_chosen_greeting(config, profile),
                source=AgentConfigSource.LLM,
                detail=reply.model,
                template_version=template_name,
            )

        logger.warning(
            "falling back to the deterministic config template",
            extra={"tenant_id": str(tenant.id), "rejections": len(attempts)},
        )
        return GenerationOutcome(
            config=self._with_chosen_greeting(self.fallback_config(tenant, profile), profile),
            source=AgentConfigSource.TEMPLATE_FALLBACK,
            detail=template_name,
            template_version=template_name,
        )

    def _with_chosen_greeting(
        self, config: GeneratedAgentConfig, profile: BusinessProfile
    ) -> GeneratedAgentConfig:
        """Let the owner's own opening line win over the generated one.

        Applied to both paths — the model's config and the template fallback —
        so "what you previewed is what callers hear" holds however the rest of
        the configuration was produced. Only the line spoken verbatim is
        replaced; everything else the model wrote stays, and the voice still
        comes from the greeting style.
        """
        chosen = (profile.greeting_custom or "").strip()
        if not chosen:
            return config
        logger.info("using the owner's own greeting", extra={"greeting_chars": len(chosen)})
        return config.model_copy(update={"greeting": chosen})

    # ---- prompt construction --------------------------------------------
    def _render_input(self, tenant: Tenant, profile: BusinessProfile, template: str) -> str:
        values = {
            "business_name": tenant.name,
            "business_type": tenant.business_type.value,
            "services": ", ".join(profile.services) or "not specified",
            "operating_hours": profile.hours_raw or "not specified",
            "timezone": tenant.timezone,
            "greeting_style": profile.greeting_style.value,
            "escalation_rules": profile.escalation_raw or "not specified",
            "notification_email": tenant.contact_email,
        }
        if "{knowledge}" in template:
            values["knowledge"] = render_knowledge(profile.knowledge_json)
            values["caller_questions"] = render_caller_questions(profile.caller_questions_json)
        return template.format(**values)

    def _repair_prompt(self, original: str, error: str) -> str:
        return (
            f"{original}\n\n"
            "Your previous reply was rejected by the schema validator with:\n"
            f"{error}\n\n"
            "Reply again with a single valid JSON object matching the required shape."
        )

    # ---- deterministic fallback -----------------------------------------
    def fallback_config(self, tenant: Tenant, profile: BusinessProfile) -> GeneratedAgentConfig:
        """Build a usable config from the form alone, with no model involved.

        Conservative by design: weekday hours, take a message, escalate nothing
        automatically. It should never be *wrong*, only less tailored.
        """
        hours = self._fallback_hours(profile, tenant.timezone)
        greeting = _GREETING_TEMPLATES[profile.greeting_style].format(name=tenant.name)
        services = list(profile.services) or ["general enquiries"]

        return GeneratedAgentConfig(
            business_summary=(
                f"{tenant.name} is a {tenant.business_type.value.replace('_', ' ')} business."
            ),
            services=services,
            hours=hours,
            greeting=greeting,
            tone=profile.greeting_style.value,
            escalation=EscalationPolicy(notify_email=tenant.contact_email),
            call_handling_instructions=[
                f"Greet the caller and identify the business as {tenant.name}.",
                "Answer questions about services and opening hours using the information given.",
                "Take the caller's name and a callback number before ending the call.",
                "Do not quote prices, make bookings, or promise anything not listed here.",
            ],
            fallback_behavior=(
                "If you do not know the answer, say so plainly, take a message with the "
                "caller's name and callback number, and tell them someone will follow up."
            ),
        )

    def _fallback_hours(self, profile: BusinessProfile, timezone: str) -> BusinessHours:
        """Reuse hours parsed at signup, else assume a weekday business."""
        if profile.hours_json:
            try:
                return BusinessHours.model_validate(profile.hours_json)
            except ValidationError:
                logger.warning("stored hours_json failed validation; using weekday default")

        weekdays = (
            Weekday.MONDAY,
            Weekday.TUESDAY,
            Weekday.WEDNESDAY,
            Weekday.THURSDAY,
            Weekday.FRIDAY,
        )
        return BusinessHours(
            timezone=timezone,
            days=[DaySchedule(day=day, opens_at="09:00", closes_at="17:00") for day in weekdays]
            + [
                DaySchedule(day=Weekday.SATURDAY, closed=True),
                DaySchedule(day=Weekday.SUNDAY, closed=True),
            ],
        )
