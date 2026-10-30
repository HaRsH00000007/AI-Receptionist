"""The General section of Agents: the agent's name and its timezone.

Two fields that look alike in a form and behave nothing alike underneath.

``agent_name`` is a label. It is never spoken, never rendered into the prompt
and never sent to a vendor, so saving it must *not* publish a configuration —
a version whose diff is empty makes the history useless for answering "what
changed before the calls got worse?".

``timezone`` is the opposite. It is derived from the area code at signup, and
that derivation is knowingly wrong for states spanning two zones, so a business
has to be able to correct it. It is also parsed *into* the stored hours and
rendered into the prompt, which is why changing it republishes and why the
hours have to be re-parsed even when their wording did not change.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AgentConfig, BusinessProfile, Tenant
from tests.portal_support import add_member, live_owner

BASE: dict[str, Any] = {
    "services": "cuts, color, blowouts",
    "operating_hours": "Mon-Fri 9am-5pm",
    "greeting_style": "professional",
    "custom_greeting": "",
    "escalation_rules": "",
}


def _edit(**overrides: Any) -> dict[str, Any]:
    return {**BASE, **overrides}


async def _save(client: AsyncClient, owner: Any, payload: dict[str, Any]) -> Any:
    return await client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings", json=payload, headers=owner.headers
    )


async def _profile(api_app: FastAPI, tenant_id: Any) -> BusinessProfile:
    async with api_app.state.session_factory() as session:
        return (
            await session.execute(
                select(BusinessProfile).where(BusinessProfile.tenant_id == tenant_id)
            )
        ).scalar_one()


async def _live_version(api_app: FastAPI, tenant_id: Any) -> int:
    async with api_app.state.session_factory() as session:
        config = (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()
        return config.version


# ---------------------------------------------------------------------------
# The timezone list
# ---------------------------------------------------------------------------


async def test_the_offered_timezones_are_exactly_the_ones_the_api_accepts(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A client cannot be shown a choice the validator would reject."""
    from app.data.timezones import supported_timezones

    owner = await live_owner(api_client, api_app)
    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/agent/timezones", headers=owner.headers
    )
    assert response.status_code == 200, response.text
    options = response.json()

    assert {option["timezone"] for option in options} == set(supported_timezones())
    assert all(option["label"] for option in options), "every zone needs a readable name"
    # Eastern first: the list is ordered for a US business, not alphabetically.
    assert options[0]["timezone"] == "America/New_York"


async def test_the_timezone_list_needs_a_session(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    response = await api_client.get(f"/api/v1/tenants/{owner.tenant_id}/agent/timezones")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# The agent's name
# ---------------------------------------------------------------------------


async def test_naming_the_agent_saves_without_publishing_a_new_version(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The name reaches no prompt, so it must not cost a version or a model call."""
    owner = await live_owner(api_client, api_app)
    # Settle the profile first: BASE differs from what signup stored, so the
    # first save publishes on its own merits and would hide the point.
    await _save(api_client, owner, _edit())
    before = await _live_version(api_app, owner.tenant_id)

    response = await _save(api_client, owner, _edit(agent_name="Hope"))
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["config_version"] == before, "renaming must not publish"
    assert result["previous_version"] is None
    assert "no new version" in result["detail"].lower()

    profile = await _profile(api_app, owner.tenant_id)
    assert profile.agent_name == "Hope"
    assert await _live_version(api_app, owner.tenant_id) == before


async def test_the_name_is_collapsed_to_one_line(api_client: AsyncClient, api_app: FastAPI) -> None:
    """It is shown in headings, so a newline in it breaks the page."""
    owner = await live_owner(api_client, api_app)
    await _save(api_client, owner, _edit(agent_name="  Hope\n  the   receptionist  "))

    profile = await _profile(api_app, owner.tenant_id)
    assert profile.agent_name == "Hope the receptionist"


async def test_a_blank_name_clears_it_but_omitting_it_leaves_it_alone(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """ "" means remove; absent means don't touch. A client written before this
    field existed sends neither and must not wipe the name."""
    owner = await live_owner(api_client, api_app)
    await _save(api_client, owner, _edit(agent_name="Hope"))

    # Absent: unchanged.
    await _save(api_client, owner, _edit(services="cuts, color"))
    assert (await _profile(api_app, owner.tenant_id)).agent_name == "Hope"

    # Blank: cleared.
    await _save(api_client, owner, _edit(services="cuts, color", agent_name=""))
    assert (await _profile(api_app, owner.tenant_id)).agent_name is None


async def test_the_name_comes_back_on_the_profile(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    await _save(api_client, owner, _edit(agent_name="Hope"))

    response = await api_client.get(
        f"/api/v1/tenants/{owner.tenant_id}/profile", headers=owner.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["agent_name"] == "Hope"


# ---------------------------------------------------------------------------
# The timezone
# ---------------------------------------------------------------------------


async def test_changing_the_timezone_publishes_a_new_version(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Unlike the name, the zone is rendered into the prompt."""
    owner = await live_owner(api_client, api_app)
    before = await _live_version(api_app, owner.tenant_id)

    response = await _save(api_client, owner, _edit(timezone="America/New_York"))
    assert response.status_code == 200, response.text
    assert response.json()["config_version"] == before + 1

    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, owner.tenant_id)
        assert tenant is not None
        assert tenant.timezone == "America/New_York"


async def test_the_hours_are_reparsed_into_the_new_zone_even_when_the_words_do_not_change(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The stored hours carry the zone inside them.

    This is the bug the ordering in ``_apply`` exists to prevent: leaving the
    hours alone because their text matched would keep "10-7 Pacific" for a
    business that just told us it is in New York.
    """
    owner = await live_owner(api_client, api_app)
    await _save(api_client, owner, _edit())

    original = await _profile(api_app, owner.tenant_id)
    assert original.hours_json is not None, "this test needs parsed hours to be meaningful"
    assert original.hours_json["timezone"] != "America/New_York"

    # Same wording, different zone.
    response = await _save(api_client, owner, _edit(timezone="America/New_York"))
    assert response.status_code == 200, response.text

    updated = await _profile(api_app, owner.tenant_id)
    assert updated.hours_json is not None
    assert updated.hours_json["timezone"] == "America/New_York"


async def test_an_unsupported_timezone_is_refused(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Including real IANA zones the product does not reason about.

    The value ends up in the prompt and in "are they open right now", so the
    accepted set is the product's own list, not all ~600 zones.
    """
    owner = await live_owner(api_client, api_app)

    for bad in ["Mars/Olympus", "Europe/London", "EST", ""]:
        response = await _save(api_client, owner, _edit(timezone=bad))
        assert response.status_code == 422, f"{bad!r} should be refused: {response.text}"

    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, owner.tenant_id)
        assert tenant is not None
        assert tenant.timezone != "Mars/Olympus"


async def test_saving_the_same_timezone_changes_nothing(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Re-submitting the form must not burn a version."""
    owner = await live_owner(api_client, api_app)
    await _save(api_client, owner, _edit())
    settled = await _live_version(api_app, owner.tenant_id)

    async with api_app.state.session_factory() as session:
        tenant = await session.get(Tenant, owner.tenant_id)
        assert tenant is not None
        current = tenant.timezone

    response = await _save(api_client, owner, _edit(timezone=current))
    assert response.status_code == 200, response.text
    assert response.json()["config_version"] == settled


# ---------------------------------------------------------------------------
# Who may change it
# ---------------------------------------------------------------------------


async def test_a_member_cannot_rename_the_agent_or_move_its_timezone(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    member = await add_member(api_app, owner.tenant_id)

    response = await api_client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings",
        json=_edit(agent_name="Hope", timezone="America/New_York"),
        headers=member,
    )
    assert response.status_code == 403

    profile = await _profile(api_app, owner.tenant_id)
    assert profile.agent_name is None


# ---------------------------------------------------------------------------
# The sign-off
# ---------------------------------------------------------------------------


async def test_a_sign_off_is_rendered_into_the_prompt_as_an_instruction(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """There is no "last message" on a voice agent, so this is an instruction.

    Asserted on the published prompt rather than on the profile column: the
    column only matters because the prompt carries it, and a sign-off stored
    but never rendered would be a field that quietly does nothing.
    """
    owner = await live_owner(api_client, api_app)

    response = await _save(
        api_client, owner, _edit(custom_goodbye="Thanks for calling. Have a great day.")
    )

    assert response.status_code == 200, response.text
    profile = await _profile(api_app, owner.tenant_id)
    assert profile.goodbye_custom == "Thanks for calling. Have a great day."
    async with api_app.state.session_factory() as session:
        config = (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == owner.tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()
    assert "Thanks for calling. Have a great day." in config.system_prompt
    assert "Ending the call:" in config.system_prompt
    # And not as the opening line, which is the thing it is easiest to confuse
    # it with — the greeting is read verbatim, this is followed.
    assert "Have a great day." not in config.first_message


async def test_a_save_that_does_not_mention_the_sign_off_keeps_it(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The Training panel sends this shape without the field.

    If an absent value meant "" the sign-off would be deleted every time
    somebody edited their opening hours from the other panel.
    """
    owner = await live_owner(api_client, api_app)
    await _save(api_client, owner, _edit(custom_goodbye="Goodbye for now."))

    response = await _save(api_client, owner, _edit(services="cuts, color, blowouts, braids"))

    assert response.status_code == 200, response.text
    assert (await _profile(api_app, owner.tenant_id)).goodbye_custom == "Goodbye for now."


async def test_an_empty_sign_off_removes_it(api_client: AsyncClient, api_app: FastAPI) -> None:
    """Explicitly "" is the only way to clear it, and it clears the prompt too."""
    owner = await live_owner(api_client, api_app)
    await _save(api_client, owner, _edit(custom_goodbye="Goodbye for now."))

    response = await _save(api_client, owner, _edit(custom_goodbye=""))

    assert response.status_code == 200, response.text
    assert (await _profile(api_app, owner.tenant_id)).goodbye_custom is None
    async with api_app.state.session_factory() as session:
        config = (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == owner.tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()
    assert "Ending the call:" not in config.system_prompt


# ---------------------------------------------------------------------------
# Personality: how it sounds, rather than what it says
# ---------------------------------------------------------------------------


async def _pushed(api_app: FastAPI, tenant_id: Any) -> Any:
    """What the voice agent currently holds, as the fake vendor recorded it."""
    from sqlalchemy import select as _select

    from app.models import Agent

    async with api_app.state.session_factory() as session:
        agent = (
            await session.execute(_select(Agent).where(Agent.tenant_id == tenant_id))
        ).scalar_one()
        agent_id = agent.elevenlabs_agent_id
    assert agent_id
    return api_app.state.providers.elevenlabs.agents[agent_id]


async def test_the_talking_speed_reaches_the_voice_agent(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """It is the vendor's own ``tts.speed``, so the proof is on the agent."""
    owner = await live_owner(api_client, api_app)

    response = await _save(api_client, owner, _edit(speaking_speed=0.85))

    assert response.status_code == 200, response.text
    assert (await _profile(api_app, owner.tenant_id)).speaking_speed == 0.85
    assert (await _pushed(api_app, owner.tenant_id)).delivery.speed == 0.85


async def test_a_speed_the_vendor_would_refuse_is_refused_here(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """ElevenLabs rejects anything outside 0.7 to 1.2 by name — checked against
    the live API. Storing one would be a 422 on every publish from then on."""
    owner = await live_owner(api_client, api_app)

    too_fast = await _save(api_client, owner, _edit(speaking_speed=2.5))
    too_slow = await _save(api_client, owner, _edit(speaking_speed=0.5))

    assert too_fast.status_code == 422, too_fast.text
    assert too_slow.status_code == 422, too_slow.text


async def test_responsiveness_is_sent_in_the_vendors_own_words(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """``low`` is our word; ``patient`` is the only one the vendor accepts.

    It refuses ``low`` and ``high`` outright, which is why the translation
    happens before the adapter rather than inside the UI.
    """
    owner = await live_owner(api_client, api_app)

    response = await _save(api_client, owner, _edit(responsiveness="low"))

    assert response.status_code == 200, response.text
    assert (await _pushed(api_app, owner.tenant_id)).delivery.eagerness == "patient"


async def test_auto_sends_the_vendors_recommended_setting(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)

    await _save(api_client, owner, _edit(responsiveness="auto"))

    assert (await _pushed(api_app, owner.tenant_id)).delivery.eagerness == "normal"


async def test_a_receptionist_that_chose_neither_is_left_to_the_vendors_defaults(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Nothing is sent rather than "1.0" and "normal".

    A reset would otherwise be indistinguishable from a choice in the vendor's
    own record of the agent.
    """
    owner = await live_owner(api_client, api_app)

    pushed = await _pushed(api_app, owner.tenant_id)

    assert pushed.delivery is None or (
        pushed.delivery.speed is None and pushed.delivery.eagerness is None
    )


async def test_the_personality_travels_on_the_version_so_a_rollback_restores_it(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A rollback has to restore how it sounded, not only what it said."""
    from app.services.config_publishing import delivery_of_config

    owner = await live_owner(api_client, api_app)
    await _save(api_client, owner, _edit(speaking_speed=1.1, responsiveness="high"))

    async with api_app.state.session_factory() as session:
        config = (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == owner.tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()

    delivery = delivery_of_config(config.model_params_json)
    assert delivery.speed == 1.1
    assert delivery.eagerness == "eager"


async def test_changing_the_tone_publishes_and_is_kept(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The tone was only editable from the Training panel before this."""
    owner = await live_owner(api_client, api_app)
    before = await _live_version(api_app, owner.tenant_id)

    response = await _save(api_client, owner, _edit(greeting_style="formal"))

    assert response.status_code == 200, response.text
    assert (await _profile(api_app, owner.tenant_id)).greeting_style.value == "formal"
    assert await _live_version(api_app, owner.tenant_id) > before
