"""A fake LLM that returns realistic, schema-valid JSON.

The point is not to imitate a model's prose — it is to let the *orchestration*
around the model be tested honestly. So the fake reads the prompt, extracts the
business facts embedded in it, and answers with JSON that satisfies the schema.
A test can also queue a malformed reply, which is how the repair retry and the
deterministic fallback get exercised.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.providers.fakes.support import Behaviour
from app.providers.models import LLMResponse

_FIELD = re.compile(r"^([A-Za-z ]+):[ \t]*(.*)$", re.MULTILINE)


def _facts(prompt: str) -> dict[str, str]:
    """Pull the "Label: value" lines the prompt templates emit."""
    return {key.strip().lower(): value.strip() for key, value in _FIELD.findall(prompt)}


@dataclass
class FakeLLMProvider:
    """Deterministic, offline, and shaped like the real thing."""

    name: str = "fake-llm"
    behaviour: Behaviour = field(default_factory=Behaviour)
    #: Replies to return instead of the generated one, consumed in order. Use
    #: this to feed malformed JSON and prove the caller rejects it.
    scripted: list[str] = field(default_factory=list)

    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        self.behaviour.record("complete", model=model, temperature=temperature)

        if self.scripted:
            return LLMResponse(text=self.scripted.pop(0), model=model)

        text = (
            self._summary_json(user) if "transcript" in system.lower() else self._config_json(user)
        )
        return LLMResponse(text=text, model=model, input_tokens=len(user) // 4, output_tokens=200)

    # ---- generated payloads ---------------------------------------------
    def _config_json(self, user: str) -> str:
        facts = _facts(user)
        name = facts.get("business name", "The Business")
        services_raw = facts.get("services", "")
        services = [part.strip() for part in re.split(r"[,;]", services_raw) if part.strip()]

        payload: dict[str, Any] = {
            "business_summary": f"{name} is a {facts.get('business type', 'local')} business.",
            "services": services or ["general enquiries"],
            "hours": {
                "timezone": facts.get("timezone", "America/Los_Angeles"),
                "days": [
                    {"day": day, "closed": False, "opens_at": "09:00", "closes_at": "17:00"}
                    for day in (
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                    )
                ]
                + [
                    {"day": "saturday", "closed": True},
                    {"day": "sunday", "closed": True},
                ],
            },
            "greeting": f"Thank you for calling {name}. How can I help you today?",
            "tone": facts.get("greeting style", "professional"),
            "escalation": {
                "default_mode": "take_message",
                "rules": [{"when": "urgent or emergency", "mode": "notify_owner"}],
                "notify_email": facts.get("notification email") or None,
                "notify_phone": None,
            },
            "call_handling_instructions": [
                "Greet the caller and identify the business by name.",
                "Answer questions about services, hours and location.",
                "Take the caller's name and callback number before ending the call.",
            ],
            "fallback_behavior": (
                "If you cannot answer, apologise, take a message with a callback "
                "number, and tell the caller someone will follow up."
            ),
            "faq": [
                {
                    "question": "What are your hours?",
                    "answer": "We are open weekdays from 9am to 5pm.",
                }
            ],
        }
        return json.dumps(payload)

    def _summary_json(self, user: str) -> str:
        payload = {
            "summary": "The caller asked about availability and left a callback number.",
            "caller_name": "Jamie Rivera" if "Jamie" in user else None,
            "callback_number": None,
            "intent": "booking_request",
            "reason_for_call": "Wanted to book an appointment this week.",
            "key_details": ["Prefers a morning slot", "Existing customer"],
            "requested_follow_up": "Call back to confirm a time.",
            "urgency": 2,
            "needs_human": False,
            "ai_handled_successfully": True,
        }
        return json.dumps(payload)
