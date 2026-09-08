"""Signup request and response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

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
    escalation_rules: str = Field(default="", max_length=2000)
    notification_email: EmailStr
    area_code: str = Field(min_length=3, max_length=10)
    plan: SignupPlan = SignupPlan.STARTER
    contact_phone: str = Field(min_length=7, max_length=32)

    @field_validator("plan")
    @classmethod
    def _plan_is_offered(cls, value: TenantPlan) -> TenantPlan:
        if value is TenantPlan.TRIAL:
            raise ValueError("trial is assigned internally and cannot be requested")
        return value

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
