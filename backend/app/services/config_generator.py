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

from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import BusinessProfile, Tenant
from app.models.enums import AgentConfigSource, GreetingStyle
from app.prompts.loader import AGENT_CONFIG_PROMPT, load_prompt, split_sections
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


class ConfigGenerator:
    """Generates one tenant's agent configuration."""

    def __init__(self, llm: LLMProvider, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    async def generate(self, tenant: Tenant, profile: BusinessProfile) -> GenerationOutcome:
        system, user_template = split_sections(load_prompt(AGENT_CONFIG_PROMPT))
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
                config=config,
                source=AgentConfigSource.LLM,
                detail=reply.model,
                template_version=AGENT_CONFIG_PROMPT,
            )

        logger.warning(
            "falling back to the deterministic config template",
            extra={"tenant_id": str(tenant.id), "rejections": len(attempts)},
        )
        return GenerationOutcome(
            config=self.fallback_config(tenant, profile),
            source=AgentConfigSource.TEMPLATE_FALLBACK,
            detail=AGENT_CONFIG_PROMPT,
            template_version=AGENT_CONFIG_PROMPT,
        )

    # ---- prompt construction --------------------------------------------
    def _render_input(self, tenant: Tenant, profile: BusinessProfile, template: str) -> str:
        return template.format(
            business_name=tenant.name,
            business_type=tenant.business_type.value,
            services=", ".join(profile.services) or "not specified",
            operating_hours=profile.hours_raw or "not specified",
            timezone=tenant.timezone,
            greeting_style=profile.greeting_style.value,
            escalation_rules=profile.escalation_raw or "not specified",
            notification_email=tenant.contact_email,
        )

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
