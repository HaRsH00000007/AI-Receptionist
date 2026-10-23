"""Transfer calls: the rules that hand a live call to a person.

This is the first piece of customer-editable configuration that makes the
product *dial a number*, so the tests here are mostly about the ways that could
go wrong rather than about the happy path:

* a rule belonging to another business is a 404, not a 403 and never an edit;
* a member who can read the dashboard cannot point the receptionist at a phone;
* what the vendor is told matches the rules exactly — a disabled rule is not
  sent, and deleting the last rule clears the tool rather than leaving the
  previous set in force;
* a destination that is not a US number is refused before it is stored.

The fake ElevenLabs provider records the transfers it was pushed, which is what
lets the last of those be asserted on the thing that actually matters: what the
agent would do on the next call.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Agent, AgentConfig
from app.models.enums import MembershipRole
from app.services.transfer_rules import MAX_RULES
from tests.portal_support import add_member, live_owner


def _rule(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "destination_number": "(205) 555-0143",
        "condition": "the caller asks to speak to a person about billing",
        "label": "Front desk",
    }
    payload.update(overrides)
    return payload


def _url(owner: Any, rule_id: Any = None) -> str:
    base = f"/api/v1/tenants/{owner.tenant_id}/agent/transfer-rules"
    return base if rule_id is None else f"{base}/{rule_id}"


async def _add(client: AsyncClient, owner: Any, **overrides: Any) -> dict[str, Any]:
    response = await client.post(_url(owner), json=_rule(**overrides), headers=owner.headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _pushed_transfers(api_app: FastAPI, tenant_id: uuid.UUID) -> tuple[Any, ...]:
    """What the voice agent currently holds, as the fake vendor recorded it."""
    async with api_app.state.session_factory() as session:
        agent = (
            await session.execute(select(Agent).where(Agent.tenant_id == tenant_id))
        ).scalar_one()
        agent_id = agent.elevenlabs_agent_id
    assert agent_id
    return api_app.state.providers.elevenlabs.agents[agent_id].transfers


async def _live_config(api_app: FastAPI, tenant_id: uuid.UUID) -> AgentConfig:
    async with api_app.state.session_factory() as session:
        return (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


async def test_a_new_receptionist_has_no_transfer_rules(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The empty state is real: nothing is forwarded until someone says so."""
    owner = await live_owner(api_client, api_app)
    response = await api_client.get(_url(owner), headers=owner.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rules"] == []
    assert body["max_rules"] == MAX_RULES
    # The POC call path imports the number into ElevenLabs natively, which is
    # what a blind transfer needs.
    assert body["cold_transfer_available"] is True
    assert await _pushed_transfers(api_app, owner.tenant_id) == ()


async def test_the_rules_need_a_session(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    assert (await api_client.get(_url(owner))).status_code == 401


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------


async def test_adding_a_rule_publishes_it_and_tells_the_voice_agent(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    before = (await _live_config(api_app, owner.tenant_id)).version

    body = await _add(api_client, owner)

    assert len(body["rules"]) == 1
    rule = body["rules"][0]
    assert rule["destination_e164"] == "+12055550143", "stored in E.164, however it was typed"
    assert rule["transfer_type"] == "warm", "warm by default: a person is told who is calling"
    assert rule["enabled"] is True

    # A transfer rule changes what the receptionist *does*, so it gets a version.
    assert body["config_version"] == before + 1
    assert body["synced"] is True

    pushed = await _pushed_transfers(api_app, owner.tenant_id)
    assert [(target.e164, target.kind) for target in pushed] == [("+12055550143", "warm")]
    assert pushed[0].condition == _rule()["condition"]


async def test_the_published_version_records_the_destinations(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """ "Which version answered this call?" has to cover where it was sent."""
    owner = await live_owner(api_client, api_app)
    await _add(api_client, owner)

    config = await _live_config(api_app, owner.tenant_id)
    assert config.model_params_json["transfers"] == [
        {
            "phone_number": "+12055550143",
            "condition": _rule()["condition"],
            "kind": "warm",
        }
    ]
    # Copied forward, not regenerated: adding a phone number must not change a
    # word the receptionist says.
    assert config.generator_detail == "transfer rules changed"


async def test_the_prompt_never_names_the_destination(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A prompt listing the numbers would let a caller ask for one out loud."""
    owner = await live_owner(api_client, api_app)
    before = (await _live_config(api_app, owner.tenant_id)).system_prompt
    await _add(api_client, owner)

    config = await _live_config(api_app, owner.tenant_id)
    assert config.system_prompt == before
    assert "2055550143" not in config.system_prompt.replace("+1", "")


async def test_a_destination_that_is_not_a_us_number_is_refused(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    response = await api_client.post(
        _url(owner), json=_rule(destination_number="+44 20 7946 0958"), headers=owner.headers
    )
    assert response.status_code == 422, response.text
    assert await _pushed_transfers(api_app, owner.tenant_id) == ()


async def test_a_rule_needs_a_condition(api_client: AsyncClient, api_app: FastAPI) -> None:
    """Without one the vendor's model has nothing to decide on, and every call
    would be a candidate for forwarding."""
    owner = await live_owner(api_client, api_app)
    response = await api_client.post(
        _url(owner), json=_rule(condition="   "), headers=owner.headers
    )
    assert response.status_code == 422, response.text


async def test_the_number_of_rules_is_capped(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    for index in range(MAX_RULES):
        await _add(api_client, owner, condition=f"the caller mentions topic {index}")

    response = await api_client.post(_url(owner), json=_rule(), headers=owner.headers)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["max_rules"] == MAX_RULES


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


async def test_switching_a_rule_off_stops_the_vendor_forwarding(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Off is not deleted: the rule stays on file and leaves the agent."""
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    rule_id = created["rules"][0]["id"]

    response = await api_client.patch(
        _url(owner, rule_id), json={"enabled": False}, headers=owner.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["rules"][0]["enabled"] is False, "still on file"
    assert await _pushed_transfers(api_app, owner.tenant_id) == (), "and not on the agent"


async def test_an_edit_that_changes_nothing_publishes_nothing(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    rule_id = created["rules"][0]["id"]
    before = created["config_version"]

    response = await api_client.patch(
        _url(owner, rule_id), json={"enabled": True}, headers=owner.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["config_version"] == before
    assert "nothing to publish" in response.json()["detail"].lower()


async def test_changing_the_destination_reaches_the_agent(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    rule_id = created["rules"][0]["id"]

    response = await api_client.patch(
        _url(owner, rule_id),
        json={"destination_number": "2055550199", "transfer_type": "cold"},
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text

    pushed = await _pushed_transfers(api_app, owner.tenant_id)
    assert [(target.e164, target.kind) for target in pushed] == [("+12055550199", "cold")]


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------


async def test_deleting_the_last_rule_clears_the_tool(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The failure this guards: a vendor left holding the old set would go on
    forwarding callers to a number the owner believes they removed."""
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    rule_id = created["rules"][0]["id"]

    response = await api_client.delete(_url(owner, rule_id), headers=owner.headers)
    assert response.status_code == 200, response.text
    assert response.json()["rules"] == []
    assert await _pushed_transfers(api_app, owner.tenant_id) == ()


async def test_deleting_one_of_two_leaves_the_other_in_force(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    first = await _add(api_client, owner, condition="the caller asks about billing")
    await _add(api_client, owner, destination_number="2055550177", condition="an emergency")

    await api_client.delete(_url(owner, first["rules"][0]["id"]), headers=owner.headers)

    pushed = await _pushed_transfers(api_app, owner.tenant_id)
    assert [target.e164 for target in pushed] == ["+12055550177"]


# ---------------------------------------------------------------------------
# A receptionist that shares a voice agent
# ---------------------------------------------------------------------------


async def test_a_shared_agent_saves_the_rule_and_says_it_is_not_in_force(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Transfer destinations are configured on the agent, not read per call.

    A prompt change reaches a shared vertical agent fine — it reads the live
    version on every call — but one shared agent cannot hold one tenant's
    transfer destinations without holding them for every tenant on that
    vertical. So the rule is saved, nothing is pushed, and the customer is told
    plainly rather than shown a success they do not have.
    """
    owner = await live_owner(api_client, api_app)
    elevenlabs = api_app.state.providers.elevenlabs
    async with api_app.state.session_factory() as session:
        agent = (
            await session.execute(select(Agent).where(Agent.tenant_id == owner.tenant_id))
        ).scalar_one()
        agent.is_shared = True
        await session.commit()
    elevenlabs.behaviour.reset()

    body = await _add(api_client, owner)

    assert len(body["rules"]) == 1, "the rule is still saved"
    assert body["synced"] is False
    assert "shares a voice agent" in body["detail"]
    assert elevenlabs.behaviour.count("update_agent") == 0, "the shared agent is untouched"


# ---------------------------------------------------------------------------
# Who may do any of this
# ---------------------------------------------------------------------------


async def test_a_member_can_read_the_rules_but_not_change_them(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    member = await add_member(api_app, owner.tenant_id, MembershipRole.MEMBER)

    assert (await api_client.get(_url(owner), headers=member)).status_code == 200
    assert (await api_client.post(_url(owner), json=_rule(), headers=member)).status_code == 403
    rule_id = created["rules"][0]["id"]
    assert (
        await api_client.patch(_url(owner, rule_id), json={"enabled": False}, headers=member)
    ).status_code == 403
    assert (await api_client.delete(_url(owner, rule_id), headers=member)).status_code == 403


async def test_another_business_cannot_see_or_touch_a_rule(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Scoped in the query: someone else's rule id is indistinguishable from
    one that does not exist."""
    owner = await live_owner(api_client, api_app)
    created = await _add(api_client, owner)
    rule_id = created["rules"][0]["id"]

    intruder = await live_owner(api_client, api_app)

    # Their own tenant, someone else's rule id.
    stolen = f"/api/v1/tenants/{intruder.tenant_id}/agent/transfer-rules/{rule_id}"
    assert (
        await api_client.patch(stolen, json={"enabled": False}, headers=intruder.headers)
    ).status_code == 404
    assert (await api_client.delete(stolen, headers=intruder.headers)).status_code == 404

    # And the owner's tenant with their own session: not a member, so not found.
    assert (await api_client.get(_url(owner), headers=intruder.headers)).status_code == 404

    # The rule is untouched and still in force.
    pushed = await _pushed_transfers(api_app, owner.tenant_id)
    assert [target.e164 for target in pushed] == ["+12055550143"]
