"""Read models for the status and admin APIs.

Separate from the ORM on purpose: an endpoint returns exactly these fields, so
adding a column to a table cannot accidentally start publishing it. Nothing here
carries a prompt, a credential, or a transcript.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.models.enums import (
    AgentStatus,
    CallStatus,
    GreetingStyle,
    PhoneNumberStatus,
    ProvisioningStatus,
    StepStatus,
    TenantStatus,
)
from app.schemas.business import BusinessHours, EscalationPolicy


class TenantView(BaseModel):
    id: uuid.UUID
    name: str
    business_type: str
    status: TenantStatus
    plan: str
    timezone: str
    contact_email: str
    area_code: str | None
    created_at: datetime


class BusinessProfileView(BaseModel):
    """What the receptionist knows about the business, as the business sees it.

    Read-only. The system prompt is deliberately absent; ``greeting`` is the
    live config's opening line — the words every caller already hears, so the
    business is entitled to read them.

    The normalized ``hours`` and ``escalation`` are ``None`` until they exist:
    signup only fills them when the free text matched a known pattern, and the
    config step fills the rest. The ``*_raw`` text is always what was submitted.
    """

    services: list[str]
    hours_raw: str | None
    hours: BusinessHours | None
    greeting_style: GreetingStyle
    escalation_raw: str | None
    escalation: EscalationPolicy | None
    greeting: str | None
    config_version: int | None


class StepView(BaseModel):
    step_name: str
    status: StepStatus
    attempt: int
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class BillingView(BaseModel):
    """The minimum billing state the status page needs.

    Deliberately small. It answers one question — "is this tenant entitled, and
    if not why not?" — because that is what turns an otherwise silent
    BILLING_BLOCKED run into something a customer can act on.

    No Stripe identifiers. A customer id or subscription id is a processor
    handle with no meaning to the browser, and putting one in an API response
    only widens what a leaked response discloses.
    """

    #: The authoritative answer, computed server-side from the subscription
    #: row. The client is never trusted to assert this.
    entitled: bool
    plan: str | None = None
    status: str | None = None
    trial_ends_at: datetime | None = None
    #: Why provisioning is blocked, when it is. A stable code, not a sentence.
    reason: str | None = None


class ProvisioningView(BaseModel):
    """The status page's whole payload."""

    run_id: uuid.UUID
    tenant_id: uuid.UUID
    status: ProvisioningStatus
    current_step: str | None
    attempt: int
    last_error: str | None
    correlation_id: str
    next_attempt_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    steps: list[StepView]
    #: Steps succeeded / total. Cheaper for a UI than deriving it.
    completed_steps: int
    total_steps: int
    #: Present so a run parked on billing can explain itself rather than
    #: appearing stuck.
    billing: BillingView | None = None


class PhoneNumberView(BaseModel):
    e164: str
    status: PhoneNumberStatus
    area_code: str | None
    purchased_at: datetime | None
    released_at: datetime | None


class AgentView(BaseModel):
    status: AgentStatus
    #: The vendor's id. Safe to show an operator; it is not a credential.
    elevenlabs_agent_id: str | None
    config_version: int | None
    voice_id: str | None
    generated_by: str | None
    synced_at: datetime | None


class CallView(BaseModel):
    """No transcript. The summary is the product; the raw text is not shown."""

    id: uuid.UUID
    provider_call_id: str
    direction: str
    from_e164: str | None
    started_at: datetime | None
    duration_s: int | None
    status: CallStatus
    summary: str | None
    caller_name: str | None
    callback_number: str | None
    intent: str | None
    urgency: int | None


class RunSummaryView(BaseModel):
    """One row of the admin run list."""

    run_id: uuid.UUID
    tenant_id: uuid.UUID
    business_name: str
    status: ProvisioningStatus
    current_step: str | None
    attempt: int
    last_error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ActionResult(BaseModel):
    ok: bool
    detail: str
    run_id: uuid.UUID | None = None


class AgentConfigView(BaseModel):
    """One version of a tenant's agent configuration.

    The prompt body is deliberately absent from the list view: it is large, it
    is the least useful thing when scanning history, and the admin list is the
    wrong place to spray a full system prompt across a screen. Fetch a single
    version to read it.
    """

    version: int
    is_live: bool
    generated_by: str
    generator_detail: str | None
    template_version: str | None
    voice_id: str | None
    created_at: datetime


class AgentConfigDetailView(AgentConfigView):
    """A single version, including the prompt it actually carries."""

    system_prompt: str
    first_message: str
    model_params: dict[str, Any]


class UsageView(BaseModel):
    """What a tenant has used this billing period, against what they bought.

    Computed server-side from the ledger. The client is never trusted to
    calculate it: a browser that could assert its own minute count would be
    asserting its own bill.
    """

    period_start: date
    period_end: date
    call_count: int
    call_minutes: int
    included_minutes: int
    percent_used: int
    over_limit: bool
    #: True at 80% — the warning line, before anything is enforced.
    warning: bool
    #: Whether calls stop at the limit. Only the trial plan does.
    blocks_on_overage: bool
    plan: str
