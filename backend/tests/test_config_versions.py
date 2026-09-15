"""M8 — durable, versioned agent configurations.

The property being defended is that a prompt is *evidence*. Months after a call,
someone will ask what the receptionist was told to say, and the answer has to be
exact — not "whatever is live now", and not "roughly this". Everything below
follows from treating configs as append-only facts rather than as a mutable
settings blob.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidInputError, NotFoundError
from app.models import AgentConfig, AuditLog, Call
from app.models.enums import (
    AgentConfigSource,
    AuditAction,
    PhoneNumberStatus,
    WebhookProvider,
)
from app.schemas.views import AgentConfigView
from app.services.config_versions import ConfigVersionService
from app.services.webhooks import WebhookService, parse_post_call
from tests.factories import make_agent_config, make_phone_number, make_tenant


async def _tenant(session: AsyncSession):  # type: ignore[no-untyped-def]
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    return tenant


async def _publish(service: ConfigVersionService, tenant_id, prompt: str):  # type: ignore[no-untyped-def]
    return await service.publish(
        tenant_id=tenant_id,
        system_prompt=prompt,
        first_message="Thanks for calling.",
        voice_id="voice-1",
        model_params={"temperature": 0.0, "model": "claude-opus-5"},
        generated_by=AgentConfigSource.LLM,
        generator_detail="claude-opus-5",
        template_version="agent_config.v1",
    )


# ===========================================================================
# Append-only publishing
# ===========================================================================


async def test_publishing_increments_the_version_and_moves_the_live_flag(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    service = ConfigVersionService(db_session)

    first = await _publish(service, tenant.id, "prompt one")
    second = await _publish(service, tenant.id, "prompt two")

    assert first.version == 1
    assert second.version == 2
    assert second.previous_version == 1

    live = await service.live(tenant.id)
    assert live is not None
    assert live.version == 2
    # The old row survives untouched — that is the whole point of append-only.
    superseded = await service.get_version(tenant.id, 1)
    assert superseded is not None
    assert superseded.is_live is False
    assert superseded.system_prompt == "prompt one"


async def test_only_one_version_is_ever_live(db_session: AsyncSession) -> None:
    """Enforced by a partial unique index, not merely by this code.

    Two rows claiming to be live is the state in which nothing can say which
    prompt the vendor actually holds.
    """
    tenant = await _tenant(db_session)
    service = ConfigVersionService(db_session)
    for index in range(4):
        await _publish(service, tenant.id, f"prompt {index}")
    await db_session.commit()

    live = (
        (
            await db_session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant.id)
                .where(AgentConfig.is_live.is_(True))
            )
        )
        .scalars()
        .all()
    )
    assert len(live) == 1
    assert live[0].version == 4


async def test_a_version_number_is_never_reused_after_a_rollback(
    db_session: AsyncSession,
) -> None:
    """Reuse would make two different prompts share one identity.

    Every call that cited v3 would silently start pointing at a different
    prompt — which destroys exactly the traceability the table exists for.
    """
    tenant = await _tenant(db_session)
    service = ConfigVersionService(db_session)
    await _publish(service, tenant.id, "one")
    await _publish(service, tenant.id, "two")
    await _publish(service, tenant.id, "three")

    await service.rollback_to(tenant_id=tenant.id, version=1)
    published = await _publish(service, tenant.id, "four")

    # Next is 4, not 2 — the highest ever issued, not the highest currently live.
    assert published.version == 4


# ===========================================================================
# Rollback
# ===========================================================================


async def test_rollback_makes_an_earlier_version_live_again(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    service = ConfigVersionService(db_session)
    await _publish(service, tenant.id, "good prompt")
    await _publish(service, tenant.id, "bad prompt")

    restored = await service.rollback_to(tenant_id=tenant.id, version=1)

    assert restored.version == 1
    assert restored.system_prompt == "good prompt"
    live = await service.live(tenant.id)
    assert live is not None and live.version == 1
    # The bad version is demoted, not deleted: history stays complete.
    bad = await service.get_version(tenant.id, 2)
    assert bad is not None and bad.is_live is False


async def test_rollback_touches_no_vendor(db_session: AsyncSession) -> None:
    """A rollback must work while ElevenLabs is down.

    The service takes no provider argument at all, which is the structural
    guarantee — there is no vendor client in scope to call, so no future edit
    can quietly add a network dependency to the recovery path.
    """
    tenant = await _tenant(db_session)
    service = ConfigVersionService(db_session)
    await _publish(service, tenant.id, "one")
    await _publish(service, tenant.id, "two")

    await service.rollback_to(tenant_id=tenant.id, version=1)
    assert not hasattr(service, "providers")


async def test_rolling_back_to_a_missing_version_is_refused(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant(db_session)
    service = ConfigVersionService(db_session)
    await _publish(service, tenant.id, "one")

    with pytest.raises(NotFoundError):
        await service.rollback_to(tenant_id=tenant.id, version=99)


async def test_rolling_back_to_the_live_version_is_refused(
    db_session: AsyncSession,
) -> None:
    """A no-op that reports success would be a misleading audit entry."""
    tenant = await _tenant(db_session)
    service = ConfigVersionService(db_session)
    await _publish(service, tenant.id, "one")

    with pytest.raises(InvalidInputError):
        await service.rollback_to(tenant_id=tenant.id, version=1)


async def test_one_tenants_rollback_cannot_reach_another(
    db_session: AsyncSession,
) -> None:
    """Version numbers are per-tenant, so v1 exists for everyone."""
    mine = await _tenant(db_session)
    theirs = await _tenant(db_session)
    service = ConfigVersionService(db_session)
    await _publish(service, theirs.id, "their prompt")

    # My tenant has no versions at all; asking for v1 must not find theirs.
    with pytest.raises(NotFoundError):
        await service.rollback_to(tenant_id=mine.id, version=1)


# ===========================================================================
# Provenance and audit
# ===========================================================================


async def test_a_config_records_what_produced_it(db_session: AsyncSession) -> None:
    """Enough to reproduce it: model, provider, temperature, template."""
    tenant = await _tenant(db_session)
    published = await _publish(ConfigVersionService(db_session), tenant.id, "prompt")

    config = published.config
    assert config.generated_by is AgentConfigSource.LLM
    assert config.generator_detail == "claude-opus-5"
    assert config.template_version == "agent_config.v1"
    assert config.model_params_json["temperature"] == 0.0
    assert config.model_params_json["model"] == "claude-opus-5"


async def test_publishing_and_rollback_are_both_audited(
    db_session: AsyncSession,
) -> None:
    """ "Who changed this prompt, and when?" is an audit question."""
    tenant = await _tenant(db_session)
    service = ConfigVersionService(db_session)
    await _publish(service, tenant.id, "one")
    await _publish(service, tenant.id, "two")
    await service.rollback_to(tenant_id=tenant.id, version=1)
    await db_session.commit()

    actions = [
        row.action
        for row in (
            (await db_session.execute(select(AuditLog).where(AuditLog.tenant_id == tenant.id)))
            .scalars()
            .all()
        )
    ]
    assert actions.count(AuditAction.CONFIG_CREATED) == 2
    assert AuditAction.CONFIG_ROLLED_BACK in actions


# ===========================================================================
# The call linkage — an answer that must not drift
# ===========================================================================


async def test_a_call_keeps_the_version_it_was_served_by(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stamped at ingestion, not joined at read time.

    Joining "the live config" later would answer with whatever is live *then*,
    so a prompt published after the call would be blamed for what it said.
    """
    tenant = await _tenant(db_session)
    number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
    db_session.add(number)
    service = ConfigVersionService(db_session)
    await _publish(service, tenant.id, "the prompt that was live")
    await db_session.commit()

    payload = {
        "conversation_id": "conv_versioned",
        "data": {
            "conversation_id": "conv_versioned",
            "metadata": {
                "phone_call": {"agent_number": number.e164, "external_number": "+15551110000"},
                "call_duration_secs": 42,
            },
            "transcript": [],
        },
    }
    parsed = parse_post_call(payload)
    assert parsed is not None

    webhooks = WebhookService(db_session)
    event = await webhooks.record(
        provider=WebhookProvider.ELEVENLABS,
        event_type="post_call",
        event_id="conv_versioned",
        payload=payload,
        signature_valid=True,
        correlation_id="test",
    )
    assert event is not None
    await webhooks.ingest_post_call(event, parsed)

    call = (
        await db_session.execute(select(Call).where(Call.provider_call_id == "conv_versioned"))
    ).scalar_one()
    assert call.agent_config_version == 1

    # Publishing a new version must not rewrite history.
    await _publish(service, tenant.id, "a later prompt")
    await db_session.commit()
    await db_session.refresh(call)
    assert call.agent_config_version == 1


# ===========================================================================
# Admin surface
# ===========================================================================


async def test_the_config_history_endpoint_is_reachable(
    api_client: AsyncClient,
) -> None:
    """A tenant with no configs answers with an empty list, not an error."""
    response = await api_client.get(f"/api/v1/admin/tenants/{uuid.uuid4()}/configs")
    assert response.status_code == 200
    assert response.json() == []


async def test_the_list_view_withholds_the_prompt_body(db_session: AsyncSession) -> None:
    """A history list is for scanning, not for spraying prompts across a screen."""
    tenant = await _tenant(db_session)
    db_session.add(make_agent_config(tenant, is_live=True))
    await db_session.commit()

    assert "system_prompt" not in AgentConfigView.model_fields
