"""Read models for the status and admin APIs.

Separate from the ORM on purpose: an endpoint returns exactly these fields, so
adding a column to a table cannot accidentally start publishing it. Nothing here
carries a prompt, a credential, or a transcript.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import (
    AgentStatus,
    CallStatus,
    PhoneNumberStatus,
    ProvisioningStatus,
    StepStatus,
    TenantStatus,
)


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
