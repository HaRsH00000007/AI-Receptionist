"""M7 — shared vertical agents.

Sharing one vendor agent between tenants buys a great deal: a prompt improvement
ships once instead of once per customer. It also creates a class of failure the
per-tenant shape simply does not have — an operation scoped to *one* tenant that
damages *all* of them.

Two of those are guarded here, and they are the reason this module exists:

* compensation for a failed signup must not delete the shared agent;
* an operator resyncing one tenant must not overwrite the shared prompt.

The third theme is isolation. A shared agent is a generic receptionist until
``/voice/init`` tells it whose call it is, so the isolation lives entirely in
that lookup — and the tests below check that one tenant's configuration can
never be served for another tenant's number.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent
from app.models.enums import AgentMode, AgentStatus, BusinessType, PhoneNumberStatus, TenantStatus
from app.provisioning.compensation import compensate_run
from app.services.shared_agents import (
    effective_agent_mode,
    parse_shared_agent_map,
    resolve_shared_agent,
)
from tests.factories import (
    make_agent,
    make_agent_config,
    make_phone_number,
    make_run,
    make_tenant,
)
from tests.support import build_settings

SHARED_MAP = "salon:agent_salon,legal:agent_legal,generic:agent_generic"


# ===========================================================================
# Configuration
# ===========================================================================


def test_the_shared_agent_map_is_parsed() -> None:
    assert parse_shared_agent_map(SHARED_MAP) == {
        "salon": "agent_salon",
        "legal": "agent_legal",
        "generic": "agent_generic",
    }


def test_a_malformed_map_entry_is_skipped_rather_than_fatal() -> None:
    """This is read on the call path; a stray comma must not stop a call."""
    assert parse_shared_agent_map("salon:agent_a,,broken,legal:agent_b, :x, y:") == {
        "salon": "agent_a",
        "legal": "agent_b",
    }


def test_an_unmapped_vertical_falls_back_to_generic() -> None:
    """Adding a business type must not leave it unserved."""
    settings = build_settings(shared_agent_ids=SHARED_MAP)
    resolved = resolve_shared_agent(settings, BusinessType.MEDICAL)
    assert resolved is not None
    assert resolved.agent_id == "agent_generic"
    assert resolved.vertical == "generic"


def test_a_vertical_with_its_own_agent_is_preferred_over_generic() -> None:
    settings = build_settings(shared_agent_ids=SHARED_MAP)
    resolved = resolve_shared_agent(settings, BusinessType.SALON)
    assert resolved is not None
    assert resolved.agent_id == "agent_salon"


def test_no_configuration_means_no_shared_agent() -> None:
    """`None` is a normal answer: this deployment uses dedicated agents."""
    assert resolve_shared_agent(build_settings(), BusinessType.SALON) is None


def test_nothing_here_can_create_a_shared_agent() -> None:
    """Two agents serving one vertical is an invisible split-brain.

    The module exposes only resolution, never creation, so there is no code path
    that could produce a second agent for a vertical.
    """
    import app.services.shared_agents as module

    assert not [name for name in dir(module) if name.startswith("create")]


# ===========================================================================
# Mode selection — migration happens in batches
# ===========================================================================


def test_a_tenant_keeps_its_own_mode() -> None:
    """The per-tenant column is what lets a migration move batches.

    Flipping everyone at once removes the only opportunity to verify a batch
    with a test call before moving the next one.
    """
    settings = build_settings(shared_agent_ids=SHARED_MAP)
    assert effective_agent_mode(settings, AgentMode.PER_TENANT) is AgentMode.PER_TENANT
    assert effective_agent_mode(settings, AgentMode.SHARED_VERTICAL) is AgentMode.SHARED_VERTICAL


def test_a_shared_tenant_downgrades_when_nothing_is_configured() -> None:
    """Losing a configuration entry must not strand migrated tenants."""
    settings = build_settings(shared_agent_ids="")
    assert effective_agent_mode(settings, AgentMode.SHARED_VERTICAL) is AgentMode.PER_TENANT


def test_the_poc_topology_remains_the_default() -> None:
    assert build_settings().default_agent_mode == "per_tenant"


# ===========================================================================
# The guard that matters most: one tenant's failure is not everyone's
# ===========================================================================


async def test_compensation_never_deletes_a_shared_agent(api_app: FastAPI) -> None:
    """The worst thing this system could do to itself.

    Deleting the shared agent to compensate one failed signup would take down
    every other tenant on that vertical at once.
    """
    async with api_app.state.session_factory() as session:
        tenant = make_tenant(agent_mode=AgentMode.SHARED_VERTICAL)
        session.add(tenant)
        await session.flush()
        config = make_agent_config(tenant, is_live=True)
        session.add(config)
        await session.flush()
        agent = make_agent(
            tenant,
            config,
            status=AgentStatus.ACTIVE,
            elevenlabs_agent_id="agent_salon",
            is_shared=True,
        )
        run = make_run(tenant)
        session.add_all([agent, run])
        await session.commit()

        report = await compensate_run(
            session,
            run=run,
            tenant=tenant,
            providers=api_app.state.providers,
            settings=api_app.state.settings,
        )

        assert report["deleted_agent"] is None
        assert report["detached_shared_agent"] == "agent_salon"
        # The tenant is detached; the agent was never touched at the vendor.
        await session.refresh(agent)
        assert agent.status is AgentStatus.DELETED


async def test_compensation_still_deletes_a_dedicated_agent(api_app: FastAPI) -> None:
    """The guard must not weaken the per-tenant path it sits beside."""
    async with api_app.state.session_factory() as session:
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()
        config = make_agent_config(tenant, is_live=True)
        session.add(config)
        await session.flush()
        run = make_run(tenant)
        session.add_all(
            [
                run,
                make_agent(
                    tenant,
                    config,
                    status=AgentStatus.ACTIVE,
                    elevenlabs_agent_id="agent_private",
                    is_shared=False,
                ),
            ]
        )
        await session.commit()

        report = await compensate_run(
            session,
            run=run,
            tenant=tenant,
            providers=api_app.state.providers,
            settings=api_app.state.settings,
        )
        assert report["deleted_agent"] == "agent_private"


# ===========================================================================
# Many tenants, one agent
# ===========================================================================


async def test_many_tenants_may_share_one_agent_id(db_session: AsyncSession) -> None:
    """The old global unique constraint would have made sharing impossible."""
    for _ in range(3):
        tenant = make_tenant(agent_mode=AgentMode.SHARED_VERTICAL)
        db_session.add(tenant)
        await db_session.flush()
        config = make_agent_config(tenant, is_live=True)
        db_session.add(config)
        await db_session.flush()
        db_session.add(
            make_agent(
                tenant,
                config,
                status=AgentStatus.ACTIVE,
                elevenlabs_agent_id="agent_salon",
                is_shared=True,
            )
        )
    await db_session.commit()

    rows = (
        (await db_session.execute(select(Agent).where(Agent.elevenlabs_agent_id == "agent_salon")))
        .scalars()
        .all()
    )
    assert len(rows) == 3


async def test_two_tenants_still_cannot_share_a_dedicated_agent(
    db_session: AsyncSession,
) -> None:
    """The partial index keeps the failure the old constraint caught.

    Two tenants adopting one *private* agent means each would overwrite the
    other's prompt on every resync.
    """
    from sqlalchemy.exc import IntegrityError

    for _ in range(2):
        tenant = make_tenant()
        db_session.add(tenant)
        await db_session.flush()
        config = make_agent_config(tenant, is_live=True)
        db_session.add(config)
        await db_session.flush()
        db_session.add(
            make_agent(
                tenant,
                config,
                status=AgentStatus.ACTIVE,
                elevenlabs_agent_id="agent_private",
                is_shared=False,
            )
        )

    with pytest.raises(IntegrityError):
        await db_session.commit()


# ===========================================================================
# Isolation — the property sharing puts at risk
# ===========================================================================


async def test_each_tenant_on_a_shared_agent_gets_its_own_variables(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The whole isolation story for shared agents, in one test.

    Both tenants are served by the same vendor agent. What makes one a salon's
    receptionist and the other a law firm's is the dialled number, and nothing
    in the request can override it.
    """
    async with api_app.state.session_factory() as session:
        numbers: dict[str, str] = {}
        for name, business_type in (
            ("Sunset Salon", BusinessType.SALON),
            ("Careful Legal", BusinessType.LEGAL),
        ):
            tenant = make_tenant(
                name=name,
                business_type=business_type,
                status=TenantStatus.ACTIVE,
                agent_mode=AgentMode.SHARED_VERTICAL,
            )
            session.add(tenant)
            await session.flush()
            config = make_agent_config(
                tenant, is_live=True, first_message=f"Thanks for calling {name}."
            )
            session.add(config)
            await session.flush()
            number = make_phone_number(tenant, status=PhoneNumberStatus.ACTIVE)
            session.add_all(
                [
                    number,
                    make_agent(
                        tenant,
                        config,
                        status=AgentStatus.ACTIVE,
                        elevenlabs_agent_id="agent_one_for_all",
                        is_shared=True,
                    ),
                ]
            )
            numbers[name] = number.e164
        await session.commit()

    for name, e164 in numbers.items():
        response = await api_client.post("/api/v1/voice/init", json={"agent_number": e164})
        variables = response.json()["dynamic_variables"]
        assert variables["business_name"] == name
        assert variables["greeting"] == f"Thanks for calling {name}."

    # And neither leaked into the other.
    salon = await api_client.post(
        "/api/v1/voice/init", json={"agent_number": numbers["Sunset Salon"]}
    )
    assert "Careful Legal" not in salon.text
