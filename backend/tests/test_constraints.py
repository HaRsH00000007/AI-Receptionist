"""The constraints that make the provisioning workflow safe to retry.

Every test here writes two rows that must not coexist and asserts the database
refuses the second one. These are not style checks: each corresponds to a real
way the Make.com chain lost money or corrupted state, and each must hold even
when the application layer has a bug.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    AgentStatus,
    PhoneNumberStatus,
    ProvisioningStatus,
    ProvisioningStep,
    TenantStatus,
)
from tests import factories


async def _persist(session: AsyncSession, *entities: object) -> None:
    session.add_all(list(entities))
    await session.commit()


async def test_tenant_cannot_hold_two_live_signups(db_session: AsyncSession) -> None:
    """The database half of "the same form twice does not buy two numbers"."""
    first = factories.make_tenant(contact_email="owner@salon.example.com")
    await _persist(db_session, first)

    duplicate = factories.make_tenant(contact_email="owner@salon.example.com")
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_cancelled_tenant_frees_the_email(db_session: AsyncSession) -> None:
    """A business that left may legitimately come back."""
    gone = factories.make_tenant(
        contact_email="owner@salon.example.com", status=TenantStatus.CANCELLED
    )
    await _persist(db_session, gone)

    returning = factories.make_tenant(contact_email="owner@salon.example.com")
    await _persist(db_session, returning)

    assert returning.id != gone.id


async def test_tenant_cannot_own_two_active_numbers(db_session: AsyncSession) -> None:
    """The constraint the plan names explicitly. An extra number bills forever."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    first = factories.make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
    await _persist(db_session, first)

    second = factories.make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
    db_session.add(second)
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_released_number_frees_the_tenant_slot(db_session: AsyncSession) -> None:
    """Reprovisioning after compensation must not trip over the old row."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    released = factories.make_phone_number(
        tenant,
        status=PhoneNumberStatus.RELEASED,
        released_at=datetime.now(UTC),
    )
    await _persist(db_session, released)

    replacement = factories.make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
    await _persist(db_session, replacement)

    assert replacement.status is PhoneNumberStatus.ACTIVE


async def test_twilio_sid_is_globally_unique(db_session: AsyncSession) -> None:
    """Adopting a number twice under two tenants would double-bill it."""
    one, two = factories.make_tenant(), factories.make_tenant()
    await _persist(db_session, one, two)

    await _persist(db_session, factories.make_phone_number(one, twilio_sid="PNshared"))

    db_session.add(factories.make_phone_number(two, twilio_sid="PNshared"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_purchased_number_must_carry_its_sid(db_session: AsyncSession) -> None:
    """A row that claims to be provisioned must reference something real."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    db_session.add(
        factories.make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE, twilio_sid=None)
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_pending_number_may_omit_its_sid(db_session: AsyncSession) -> None:
    """The pre-write that makes a purchase recoverable after a crash."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    pending = factories.make_phone_number(tenant, status=PhoneNumberStatus.PENDING, twilio_sid=None)
    await _persist(db_session, pending)

    assert pending.twilio_sid is None


async def test_released_number_must_record_when(db_session: AsyncSession) -> None:
    """``released_at`` is the reaper's evidence that it acted."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    db_session.add(
        factories.make_phone_number(tenant, status=PhoneNumberStatus.RELEASED, released_at=None)
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_step_exists_once_per_run(db_session: AsyncSession) -> None:
    """ "UNIQUE (run_id, step_name)" - retries update, they do not append."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)
    run = factories.make_run(tenant)
    await _persist(db_session, run)

    await _persist(db_session, factories.make_step(run, ProvisioningStep.PURCHASE_NUMBER))

    db_session.add(factories.make_step(run, ProvisioningStep.PURCHASE_NUMBER))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_idempotency_keys_are_globally_unique(db_session: AsyncSession) -> None:
    """Two operations sharing a key would let one adopt the other's side effect."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)
    run = factories.make_run(tenant)
    await _persist(db_session, run)

    await _persist(
        db_session,
        factories.make_step(run, ProvisioningStep.VALIDATE, idempotency_key="shared"),
    )

    db_session.add(
        factories.make_step(run, ProvisioningStep.CREATE_AGENT, idempotency_key="shared")
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_one_in_flight_run_per_tenant(db_session: AsyncSession) -> None:
    """The distributed lock, in Postgres instead of Redis.

    Two workers cannot both start provisioning the same tenant, so they cannot
    both reach the purchase step.
    """
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    await _persist(db_session, factories.make_run(tenant, status=ProvisioningStatus.DRAFT))

    db_session.add(factories.make_run(tenant, status=ProvisioningStatus.NUMBER_PURCHASED))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.parametrize(
    "terminal",
    [ProvisioningStatus.ACTIVE, ProvisioningStatus.FAILED, ProvisioningStatus.COMPENSATED],
)
async def test_finished_run_allows_a_new_one(
    db_session: AsyncSession, terminal: ProvisioningStatus
) -> None:
    """Retrying a failed tenant from the admin panel must not hit the index."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    await _persist(db_session, factories.make_run(tenant, status=terminal))
    retry = factories.make_run(tenant, status=ProvisioningStatus.DRAFT)
    await _persist(db_session, retry)

    assert retry.status is ProvisioningStatus.DRAFT


async def test_provider_call_id_is_unique(db_session: AsyncSession) -> None:
    """Webhook replay safety: a redelivery must not create a second call."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    await _persist(db_session, factories.make_call(tenant, provider_call_id="conv_1"))

    db_session.add(factories.make_call(tenant, provider_call_id="conv_1"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.parametrize("urgency", [0, 6, -1])
async def test_urgency_is_bounded(db_session: AsyncSession, urgency: int) -> None:
    """The summarizer emits 1-5; anything else is a bug we want to see."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    db_session.add(factories.make_call(tenant, urgency=urgency))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_negative_call_duration_is_rejected(db_session: AsyncSession) -> None:
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    db_session.add(factories.make_call(tenant, duration_s=-1))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_one_live_config_per_tenant(db_session: AsyncSession) -> None:
    """Two live configs and nothing can say which prompt the vendor holds."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    await _persist(db_session, factories.make_agent_config(tenant, version=1, is_live=True))

    db_session.add(factories.make_agent_config(tenant, version=2, is_live=True))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_config_versions_are_unique_per_tenant(db_session: AsyncSession) -> None:
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    await _persist(db_session, factories.make_agent_config(tenant, version=1))

    db_session.add(factories.make_agent_config(tenant, version=1))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_one_profile_per_tenant(db_session: AsyncSession) -> None:
    """One duplicated form row is what silently corrupted every later step."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)

    await _persist(db_session, factories.make_business_profile(tenant))

    db_session.add(factories.make_business_profile(tenant))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_one_active_agent_per_tenant(db_session: AsyncSession) -> None:
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)
    config = factories.make_agent_config(tenant)
    await _persist(db_session, config)

    await _persist(db_session, factories.make_agent(tenant, config, status=AgentStatus.ACTIVE))

    db_session.add(factories.make_agent(tenant, config, status=AgentStatus.ACTIVE))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_live_config_cannot_be_deleted_while_an_agent_uses_it(
    db_session: AsyncSession,
) -> None:
    """Config history is the answer to "what prompt was live for this call?"."""
    tenant = factories.make_tenant()
    await _persist(db_session, tenant)
    config = factories.make_agent_config(tenant)
    await _persist(db_session, config)
    await _persist(db_session, factories.make_agent(tenant, config))

    await db_session.delete(config)
    with pytest.raises(IntegrityError):
        await db_session.commit()
