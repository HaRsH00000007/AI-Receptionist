"""Tally form → :class:`SignupRequest`.

Isolated in one module on purpose. Tally sends a list of ``{label, key, value}``
objects whose labels change whenever someone edits the form, and whose choice
fields arrive as option ids rather than text. Confining that mess here means the
rest of the system only ever sees one signup contract, and a form edit breaks
one mapping instead of the domain.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.errors import InvalidInputError
from app.models.enums import BusinessType, GreetingStyle, TenantPlan
from app.schemas.signup import SignupRequest, TallyWebhook
from app.services.normalization import area_code_of, normalize_phone


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


#: Label fragments we accept for each signup field, most specific first.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "business_name": ("business_name", "company_name", "name_of_business"),
    "business_type": ("business_type", "industry", "type_of_business"),
    "services": ("services", "services_offered", "what_services"),
    "operating_hours": ("operating_hours", "business_hours", "hours"),
    "greeting_style": ("greeting_style", "tone", "greeting"),
    "escalation_rules": ("escalation_rules", "escalation", "when_to_escalate"),
    "notification_email": ("notification_email", "email", "contact_email"),
    "area_code": ("area_code", "preferred_area_code"),
    "plan": ("plan", "subscription", "package"),
    "contact_phone": ("contact_phone", "phone", "phone_number", "contact_number"),
}


def _flatten(value: Any) -> str:
    """Reduce a Tally value to text.

    Choice fields arrive as a list of option ids; multi-line fields as strings.
    Anything else is stringified rather than dropped, so a mapping miss shows up
    as a validation error naming the field instead of a silent empty value.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_flatten(item) for item in value if item is not None)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("label") or value.get("value") or "")
    return str(value)


def _extract(webhook: TallyWebhook) -> dict[str, str]:
    """Map submitted fields onto our field names."""
    found: dict[str, str] = {}
    for field in webhook.fields():
        slug = _slug(field.label or field.key)
        for target, aliases in _FIELD_ALIASES.items():
            if target in found:
                continue
            if any(alias in slug for alias in aliases):
                found[target] = _flatten(field.value).strip()
                break
    return found


def _match_enum[EnumT: (BusinessType, GreetingStyle, TenantPlan)](
    raw: str, enum_class: type[EnumT], default: EnumT
) -> EnumT:
    """Resolve free text onto an enum member, falling back to the default.

    A fallback rather than an error because a business type we do not recognise
    is exactly what ``other`` is for — and losing the whole signup over a
    renamed dropdown option would be worse than a slightly generic template.
    """
    candidate = _slug(raw)
    for member in enum_class:
        if candidate == member.value or member.value in candidate:
            return member
    return default


def tally_to_signup(webhook: TallyWebhook) -> SignupRequest:
    """Build a signup from a Tally submission, or say precisely what is missing."""
    fields = _extract(webhook)

    missing = [
        name
        for name in ("business_name", "notification_email", "contact_phone", "operating_hours")
        if not fields.get(name)
    ]
    if missing:
        raise InvalidInputError(
            "tally submission is missing required fields",
            details={"missing": missing, "event_id": webhook.eventId},
        )

    # Tally's area-code question is optional; when it is absent the caller's own
    # number is the best available signal, and it is already a US number.
    area_code = fields.get("area_code", "").strip()
    if not area_code:
        area_code = area_code_of(normalize_phone(fields["contact_phone"]))

    return SignupRequest(
        business_name=fields["business_name"],
        business_type=_match_enum(
            fields.get("business_type", ""), BusinessType, BusinessType.OTHER
        ),
        services=fields.get("services", "general enquiries"),
        operating_hours=fields["operating_hours"],
        greeting_style=_match_enum(
            fields.get("greeting_style", ""), GreetingStyle, GreetingStyle.PROFESSIONAL
        ),
        escalation_rules=fields.get("escalation_rules", ""),
        notification_email=fields["notification_email"],
        area_code=area_code,
        plan=_match_enum(fields.get("plan", ""), TenantPlan, TenantPlan.STARTER),
        contact_phone=fields["contact_phone"],
    )
