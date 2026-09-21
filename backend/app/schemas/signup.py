"""Signup request and response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.models.enums import (
    BusinessType,
    GreetingStyle,
    ProvisioningStatus,
    TenantPlan,
    TenantStatus,
)

#: The plans the public form offers. ``TRIAL`` exists on the tenant model as an
#: internal default and is deliberately not selectable.
SignupPlan = TenantPlan


class SignupRequest(BaseModel):
    """One submitted signup form.

    ``extra="forbid"`` so a renamed or stray field is a 422 rather than a value
    silently dropped on its way to the database — which is the exact failure
    mode that made the spreadsheet version untrustworthy.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    business_name: str = Field(min_length=1, max_length=200)
    business_type: BusinessType
    services: str | list[str] = Field(
        description="Comma-separated text from the form, or an already-split list."
    )
    operating_hours: str = Field(min_length=1, max_length=1000)
    greeting_style: GreetingStyle = GreetingStyle.PROFESSIONAL
    #: The exact opening line callers hear, whether picked from the offered
    #: greetings or typed by the owner. Blank means "write one for me", which is
    #: what every signup did before this field existed.
    #:
    #: The cap matches `GeneratedAgentConfig.greeting`: a longer line would be
    #: accepted here and then rejected at generation time, which is a confusing
    #: place to hear about it. A test pins the two together.
    custom_greeting: str = Field(default="", max_length=300)
    escalation_rules: str = Field(default="", max_length=2000)
    notification_email: EmailStr
    area_code: str = Field(min_length=3, max_length=10)
    #: The number the customer picked from the ones offered, in E.164. Blank
    #: means "choose one for me", which is what every signup did before numbers
    #: were shown. It may be in a different area code than ``area_code`` above:
    #: when the requested code has no inventory the form offers nearby ones.
    selected_number: str = Field(default="", max_length=20)
    plan: SignupPlan = SignupPlan.STARTER
    contact_phone: str = Field(min_length=7, max_length=32)
    #: The password for the owner's account, chosen on the form.
    #:
    #: ``exclude=True`` is load-bearing, not tidiness. The whole request is
    #: dumped verbatim into ``business_profiles.raw_form_json`` so a config can
    #: be re-derived later; without this the plain password would be written to
    #: that column, and from there into every backup and every debug dump of a
    #: profile. ``SecretStr`` is the second layer: it keeps the value out of
    #: tracebacks and log lines that repr the model.
    #:
    #: Optional because signups do not all come from the web form — the Tally
    #: webhook has no password field — and because an invited team member never
    #: chooses one. Blank means the account signs in by magic link, which is how
    #: every account worked before this field existed. The web form requires it.
    password: SecretStr = Field(default=SecretStr(""), exclude=True)

    @field_validator("selected_number")
    @classmethod
    def _selected_number_is_a_us_number(cls, value: str) -> str:
        """Checked rather than trusted: it arrives from a browser.

        The purchase step will try to buy exactly this string, so a malformed
        one should be refused at the form, where the message is useful, rather
        than by the vendor in the middle of provisioning.
        """
        cleaned = value.strip()
        if not cleaned:
            return ""
        if not (cleaned.startswith("+1") and len(cleaned) == 12 and cleaned[1:].isdigit()):
            raise ValueError("choose one of the numbers offered")
        return cleaned

    @field_validator("password")
    @classmethod
    def _password_is_long_enough(cls, value: SecretStr) -> SecretStr:
        """Length only. Composition rules are deliberately absent.

        NIST 800-63B dropped the mixed-character requirements because they push
        people towards `Passw0rd!` — a predictable shape that a cracker tries
        first — while making the password harder to remember. Length is the
        property that actually costs an attacker work.

        Not stripped: a leading or trailing space a person typed on purpose is
        part of their password, and silently trimming it here would lock them
        out at the login form, which does not trim.
        """
        from app.services.passwords import MAX_LENGTH, MIN_LENGTH

        secret = value.get_secret_value()
        if not secret:
            return value
        if len(secret) < MIN_LENGTH:
            raise ValueError(f"use at least {MIN_LENGTH} characters")
        if len(secret) > MAX_LENGTH:
            raise ValueError(f"use at most {MAX_LENGTH} characters")
        return value

    @field_validator("plan")
    @classmethod
    def _plan_is_offered(cls, value: TenantPlan) -> TenantPlan:
        if value is TenantPlan.TRIAL:
            raise ValueError("trial is assigned internally and cannot be requested")
        return value

    @field_validator("custom_greeting")
    @classmethod
    def _greeting_is_speakable(cls, value: str) -> str:
        """Collapse it to the single line the agent will actually say.

        Normalized here rather than at render time so that what is stored, what
        the owner previewed and what the caller hears are the same string.
        """
        from app.services.normalization import normalize_greeting

        return normalize_greeting(value)

    @model_validator(mode="after")
    def _services_not_empty(self) -> Self:
        """Reject input that normalizes to nothing.

        Uses the same normalizer the service uses, so ", ; " is refused here
        rather than becoming an empty list that fails much later, in the
        validate step, where the message is far less useful.
        """
        from app.services.normalization import normalize_services

        if not normalize_services(self.services):
            raise ValueError("at least one service is required")
        return self


class SignupStepView(BaseModel):
    """One provisioning step, as the status page shows it."""

    step_name: str
    status: str
    attempt: int
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SignupResponse(BaseModel):
    """What the caller gets back, enough to poll the status page."""

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    tenant_status: TenantStatus
    provisioning_status: ProvisioningStatus
    correlation_id: str
    business_name: str
    contact_email: str
    timezone: str
    #: ``False`` when this request matched an existing live signup and no new
    #: tenant was created. The response is otherwise identical, which is what
    #: makes submitting the form twice safe.
    created: bool
    #: A signed, expiring grant for this tenant's status page.
    #:
    #: The business has no account yet, so this is what lets them watch
    #: provisioning without logging in. It replaces the POC's arrangement, where
    #: the tenant UUID itself was the credential — a capability that never
    #: expired, could not be revoked, and leaked through Referer headers,
    #: browser history and screenshots.
    status_token: str


class SignupAcceptedHeaders(BaseModel):
    """Documentation-only marker for the headers a signup response carries."""

    correlation_id: str


class TallyField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str = ""
    key: str = ""
    value: Any = None


class TallyWebhook(BaseModel):
    """The envelope Tally posts.

    Kept as its own schema so the Tally shape never leaks into the domain: the
    adapter maps it onto :class:`SignupRequest` and everything downstream sees
    one contract regardless of where the form was hosted.
    """

    model_config = ConfigDict(extra="ignore")

    eventId: str = ""
    data: dict[str, Any] = Field(default_factory=dict)

    def fields(self) -> list[TallyField]:
        return [TallyField.model_validate(entry) for entry in self.data.get("fields", [])]
