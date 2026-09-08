"""Minimal builders for model instances.

Each returns a valid, unsaved object with sensible defaults so a test only has
to state the field it actually cares about. Values that must be unique are
suffixed with a counter rather than randomized, so a failure message names the
row it was about.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any

from app.models import (
    Agent,
    AgentConfig,
    BusinessProfile,
    Call,
    PhoneNumber,
    ProvisioningRun,
    ProvisioningStepRecord,
    Tenant,
)
from app.models.enums import (
    AgentConfigSource,
    BusinessType,
    ProvisioningStatus,
    ProvisioningStep,
)

_counter = itertools.count(1)


def unique(prefix: str) -> str:
    return f"{prefix}-{next(_counter)}"


def make_tenant(**overrides: Any) -> Tenant:
    values: dict[str, Any] = {
        "name": "Sunset Salon",
        "business_type": BusinessType.SALON,
        "contact_email": f"{unique('owner')}@example.com",
        "contact_phone": "+15551234567",
        "area_code": "805",
        "timezone": "America/Los_Angeles",
    }
    values.update(overrides)
    return Tenant(**values)


def make_business_profile(tenant: Tenant, **overrides: Any) -> BusinessProfile:
    values: dict[str, Any] = {
        "tenant_id": tenant.id,
        "raw_form_json": {"business_name": tenant.name},
        "services": ["cuts", "color"],
        "hours_raw": "Mon-Fri 9-6, Sat till 2, closed Sun",
    }
    values.update(overrides)
    return BusinessProfile(**values)


def make_agent_config(tenant: Tenant, **overrides: Any) -> AgentConfig:
    values: dict[str, Any] = {
        "tenant_id": tenant.id,
        "version": 1,
        "system_prompt": "You are the receptionist for Sunset Salon.",
        "first_message": "Thanks for calling Sunset Salon.",
        "generated_by": AgentConfigSource.LLM,
        "generator_detail": "claude-opus-5",
        "template_version": "salon-v1",
    }
    values.update(overrides)
    return AgentConfig(**values)


def make_phone_number(tenant: Tenant, **overrides: Any) -> PhoneNumber:
    values: dict[str, Any] = {
        "tenant_id": tenant.id,
        "e164": f"+1805555{next(_counter):04d}",
        "twilio_sid": unique("PN"),
        "area_code": "805",
    }
    values.update(overrides)
    return PhoneNumber(**values)


def make_agent(tenant: Tenant, config: AgentConfig, **overrides: Any) -> Agent:
    values: dict[str, Any] = {
        "tenant_id": tenant.id,
        "agent_config_id": config.id,
        "elevenlabs_agent_id": unique("agent"),
    }
    values.update(overrides)
    return Agent(**values)


def make_run(tenant: Tenant, **overrides: Any) -> ProvisioningRun:
    values: dict[str, Any] = {
        "tenant_id": tenant.id,
        "status": ProvisioningStatus.DRAFT,
        "correlation_id": uuid.uuid4().hex,
    }
    values.update(overrides)
    return ProvisioningRun(**values)


def make_step(
    run: ProvisioningRun,
    step_name: ProvisioningStep = ProvisioningStep.VALIDATE,
    **overrides: Any,
) -> ProvisioningStepRecord:
    values: dict[str, Any] = {
        "run_id": run.id,
        "step_name": step_name,
        "idempotency_key": unique("idem"),
    }
    values.update(overrides)
    return ProvisioningStepRecord(**values)


def make_call(tenant: Tenant, **overrides: Any) -> Call:
    values: dict[str, Any] = {
        "tenant_id": tenant.id,
        "provider_call_id": unique("conv"),
        "from_e164": "+15559998888",
        "to_e164": "+18055550100",
    }
    values.update(overrides)
    return Call(**values)
