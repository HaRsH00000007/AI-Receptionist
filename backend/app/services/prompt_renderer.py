"""Render the text the voice agent actually runs.

Deterministic on purpose. The model produced a *structure*; this turns that
structure into the system prompt and opening line. Two consequences:

* the same structure always renders the same prompt, so a config version means
  something exact;
* nothing a model wrote is handed to the voice vendor as instructions without
  passing through a shape we defined.

Voice selection lives here too, keyed off the greeting style the owner chose —
never off anything a model returned (docs/00_DECISIONS.md section 3).
"""

from __future__ import annotations

from app.core.config import Settings
from app.models.enums import GreetingStyle
from app.schemas.agent_config import GeneratedAgentConfig
from app.schemas.business import BusinessHours, EscalationMode, Weekday

_DAY_LABELS: dict[Weekday, str] = {
    Weekday.MONDAY: "Monday",
    Weekday.TUESDAY: "Tuesday",
    Weekday.WEDNESDAY: "Wednesday",
    Weekday.THURSDAY: "Thursday",
    Weekday.FRIDAY: "Friday",
    Weekday.SATURDAY: "Saturday",
    Weekday.SUNDAY: "Sunday",
}

_ESCALATION_TEXT: dict[EscalationMode, str] = {
    EscalationMode.TAKE_MESSAGE: "take a detailed message with a callback number",
    EscalationMode.NOTIFY_OWNER: "take a message and flag it as urgent for the owner",
    EscalationMode.TRANSFER: "offer to transfer the caller to a person",
}


def render_hours(hours: BusinessHours) -> str:
    """Structured hours back into the prose the agent reads aloud."""
    lines: list[str] = []
    for entry in hours.days:
        label = _DAY_LABELS[entry.day]
        if entry.closed:
            lines.append(f"- {label}: closed")
        else:
            lines.append(f"- {label}: {entry.opens_at} to {entry.closes_at}")
    return "\n".join(lines) or "- Hours not specified"


def render_system_prompt(
    *, business_name: str, business_type: str, config: GeneratedAgentConfig
) -> str:
    """The full instruction set given to the voice agent."""
    sections: list[str] = [
        f"You are the receptionist answering the phone for {business_name}, "
        f"a {business_type.replace('_', ' ')} business.",
        "",
        f"About the business: {config.business_summary}",
        "",
        "Services offered:",
        *(f"- {service}" for service in config.services),
        "",
        f"Opening hours (timezone {config.hours.timezone}):",
        render_hours(config.hours),
        "",
        f"Speak in a {config.tone} tone.",
    ]

    if config.call_handling_instructions:
        sections += ["", "How to handle a call:"]
        sections += [
            f"{index}. {instruction}"
            for index, instruction in enumerate(config.call_handling_instructions, start=1)
        ]

    if config.faq:
        sections += ["", "Questions you can answer directly:"]
        for item in config.faq:
            sections += [f"Q: {item.question}", f"A: {item.answer}"]

    sections += ["", "When a caller needs more than you can give:"]
    default_action = _ESCALATION_TEXT[config.escalation.default_mode]
    sections.append(f"- By default, {default_action}.")
    for rule in config.escalation.rules:
        sections.append(f"- If the caller mentions {rule.when}, {_ESCALATION_TEXT[rule.mode]}.")

    if config.fallback_behavior:
        sections += ["", f"If you cannot help: {config.fallback_behavior}"]

    sections += [
        "",
        "Rules you must follow:",
        "- Never invent prices, availability, staff names or policies.",
        "- Never promise anything that is not stated above.",
        "- Always try to capture the caller's name and a callback number.",
        "- If you are unsure, say so and take a message.",
    ]
    return "\n".join(sections).strip()


def render_first_message(config: GeneratedAgentConfig) -> str:
    return config.greeting.strip()


def select_voice_id(settings: Settings, greeting_style: GreetingStyle) -> str:
    """Map greeting style to an ElevenLabs voice.

    A lookup in configuration, so a voice change is an env edit and an unknown
    style degrades to the configured default rather than to whatever a model
    happened to name.
    """
    return settings.voice_map.get(greeting_style.value, settings.elevenlabs_default_voice_id)
