"""Minimal builders for model instances.

Each returns a valid, unsaved object with sensible defaults so a test only has
to state the field it actually cares about. Values that must be unique are
suffixed with a counter rather than randomized, so a failure message names the
row it was about.
"""

from __future__ import annotations

import hashlib
import itertools
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.models import (
    Agent,
    AgentConfig,
    AuditLog,
    BusinessProfile,
    Call,
    Membership,
    Notification,
    PhoneNumber,
    ProvisioningRun,
    ProvisioningStepRecord,
    Session,
    Subscription,
    Tenant,
    UsageEvent,
    User,
)
from app.models.enums import (
    ActorType,
    AgentConfigSource,
    AuditAction,
    BusinessType,
    MembershipRole,
    NotificationKind,
    ProvisioningStatus,
    ProvisioningStep,
    SubscriptionStatus,
    TenantPlan,
    UsageKind,
    UsageSource,
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


# ---------------------------------------------------------------------------
# Identity (M2)
# ---------------------------------------------------------------------------


def make_user(**overrides: Any) -> User:
    values: dict[str, Any] = {
        "email": f"{unique('user')}@example.com",
        "full_name": "Alex Rivera",
    }
    values.update(overrides)
    return User(**values)


def make_membership(user: User, tenant: Tenant, **overrides: Any) -> Membership:
    values: dict[str, Any] = {
        "user_id": user.id,
        "tenant_id": tenant.id,
        "role": MembershipRole.OWNER,
        # Accepted by default: an unaccepted membership grants nothing, and a
        # test that wants that case should have to say so explicitly.
        "accepted_at": datetime.now(UTC),
    }
    values.update(overrides)
    return Membership(**values)


def make_session(user: User, **overrides: Any) -> Session:
    values: dict[str, Any] = {
        "user_id": user.id,
        "token_hash": hashlib.sha256(unique("token").encode()).hexdigest(),
        "expires_at": datetime.now(UTC) + timedelta(days=14),
    }
    values.update(overrides)
    return Session(**values)


# ---------------------------------------------------------------------------
# Billing (M2)
# ---------------------------------------------------------------------------


def make_subscription(tenant: Tenant, **overrides: Any) -> Subscription:
    values: dict[str, Any] = {
        "tenant_id": tenant.id,
        "plan": TenantPlan.TRIAL,
        "status": SubscriptionStatus.TRIALING,
        "trial_ends_at": datetime.now(UTC) + timedelta(days=14),
    }
    values.update(overrides)
    return Subscription(**values)


# ---------------------------------------------------------------------------
# Usage (M2)
# ---------------------------------------------------------------------------


def make_usage_event(tenant: Tenant, **overrides: Any) -> UsageEvent:
    values: dict[str, Any] = {
        "tenant_id": tenant.id,
        "kind": UsageKind.CALL_MINUTES,
        "source": UsageSource.MEASURED,
        "provider": "twilio",
        "quantity": 120,
        "occurred_at": datetime.now(UTC),
    }
    values.update(overrides)
    return UsageEvent(**values)


# ---------------------------------------------------------------------------
# Audit (M2)
# ---------------------------------------------------------------------------


def make_audit_log(tenant: Tenant | None = None, **overrides: Any) -> AuditLog:
    values: dict[str, Any] = {
        "tenant_id": tenant.id if tenant is not None else None,
        "actor_type": ActorType.SYSTEM,
        "action": AuditAction.PROVISIONING_STARTED,
    }
    values.update(overrides)
    return AuditLog(**values)


# ---------------------------------------------------------------------------
# Operations (M2)
# ---------------------------------------------------------------------------


def make_notification(tenant: Tenant, **overrides: Any) -> Notification:
    values: dict[str, Any] = {
        "tenant_id": tenant.id,
        "kind": NotificationKind.CALL_SUMMARY,
        "recipient": tenant.contact_email,
        "template_id": "call_summary.v1",
    }
    values.update(overrides)
    return Notification(**values)
