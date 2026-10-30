"""Choosing the voice that answers the phone.

The catalogue is the vendor's, which is the whole point of this feature and the
source of everything that could go wrong with it:

* a voice is offered only if the call path can actually speak it — the agent
  body sends English, so a Spanish voice in the picker would be chosen by
  somebody and then answer in English;
* a choice from the catalogue has to *survive*. The old publish path re-checked
  the stored voice against the three configured style defaults, which would have
  accepted "Eric" on the way in and quietly reset the business to the style
  voice on their next unrelated edit. That is the test that matters most here;
* a vendor that is down must not block signup, so the picker falls back to the
  configured voices rather than showing nothing;
* nothing describing a voice is written by us. The name, the sentence and the
  sample come from the catalogue, because a description we invented goes stale
  the moment the vendor retunes the voice.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.core.errors import VendorError
from app.models import AgentConfig
from app.models.enums import MembershipRole
from app.providers.fakes.voice import FAKE_VOICES
from tests.portal_support import add_member, live_owner
from tests.support import build_settings

#: In the fake catalogue and deliberately *not* in ``ELEVENLABS_VOICE_MAP``.
CATALOGUE_ONLY = "cjVigY5qzO86Huf0OWal"
#: In the fake catalogue, but Arabic — the call path cannot speak it.
NOT_ENGLISH = "g3YpdjT1OTh9cunaumJs"

SETTINGS: dict[str, Any] = {
    "services": "cuts, color, blowouts",
    "operating_hours": "Mon-Fri 9am-5pm",
    "greeting_style": "professional",
    "custom_greeting": "",
    "escalation_rules": "",
}


def _url(owner: Any) -> str:
    return f"/api/v1/tenants/{owner.tenant_id}/agent/voices"


async def _save(client: AsyncClient, owner: Any, **overrides: Any) -> Any:
    return await client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings",
        json={**SETTINGS, **overrides},
        headers=owner.headers,
    )


async def _live_config(api_app: FastAPI, tenant_id: Any) -> AgentConfig:
    async with api_app.state.session_factory() as session:
        return (
            await session.execute(
                select(AgentConfig)
                .where(AgentConfig.tenant_id == tenant_id)
                .where(AgentConfig.is_live.is_(True))
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# What the picker offers
# ---------------------------------------------------------------------------


async def test_the_picker_offers_the_vendors_catalogue_not_just_the_configured_three(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)

    response = await api_client.get(_url(owner), headers=owner.headers)

    assert response.status_code == 200, response.text
    options = response.json()
    offered = {option["voice_id"] for option in options}
    assert CATALOGUE_ONLY in offered, "a voice outside the style map is still choosable"
    assert len(offered) > len(api_app.state.settings.voice_map)


async def test_a_voice_the_call_path_cannot_speak_is_not_offered(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The agent body sends English and nothing else.

    An Arabic voice in the picker would be chosen by somebody and then answer
    in English, which is worse than not offering it.
    """
    owner = await live_owner(api_client, api_app)

    response = await api_client.get(_url(owner), headers=owner.headers)

    offered = {option["voice_id"] for option in response.json()}
    assert NOT_ENGLISH not in offered
    assert NOT_ENGLISH in {voice.voice_id for voice in FAKE_VOICES}, "the fake does offer it"


async def test_every_word_describing_a_voice_comes_from_the_vendor(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Name, sentence and sample. None of it is written here.

    The name is shortened for a card — "Sarah", not "Sarah - Mature,
    Reassuring, Confident" — and the rest of it becomes the line underneath,
    beside the accent the vendor labelled it with.
    """
    owner = await live_owner(api_client, api_app)

    response = await api_client.get(_url(owner), headers=owner.headers)

    options = {option["voice_id"]: option for option in response.json()}
    sarah = options["EXAVITQu4vr4xnSDxMaL"]
    assert sarah["label"] == "Sarah"
    assert sarah["summary"] == "American · Mature, Reassuring, Confident"
    assert sarah["preview_url"] == "https://example.invalid/voices/sarah.mp3"
    assert sarah["description"].startswith("Young adult woman")


async def test_every_voice_is_given_a_face_that_matches_its_labels(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A picture of a man over a woman's voice is worse than no picture.

    The faces are drawn in the client; which one a voice gets is decided here,
    because the vendor's gender and age labels live on this side.
    """
    from app.services.avatars import FEMALE, MALE_MATURE

    owner = await live_owner(api_client, api_app)

    options = {
        option["voice_id"]: option
        for option in (await api_client.get(_url(owner), headers=owner.headers)).json()
    }

    # Sarah is labelled female; Eric is labelled male and middle aged.
    assert options["EXAVITQu4vr4xnSDxMaL"]["avatar"] in FEMALE
    assert options[CATALOGUE_ONLY]["avatar"] in MALE_MATURE


async def test_a_face_does_not_change_between_reads(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """One that moved on refresh would read as a bug."""
    owner = await live_owner(api_client, api_app)

    first = (await api_client.get(_url(owner), headers=owner.headers)).json()
    second = (await api_client.get(_url(owner), headers=owner.headers)).json()

    assert [option["avatar"] for option in first] == [option["avatar"] for option in second]
    assert all(option["avatar"] for option in first), "every voice gets one"


async def test_the_fallback_picker_claims_no_face(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Without the catalogue there are no labels, so there is nothing to match.

    Claiming a face from a voice id alone would put a man's picture over a
    woman's voice a third of the time; the client draws a neutral fallback.
    """
    owner = await live_owner(api_client, api_app)
    api_app.state.providers.elevenlabs.behaviour.fail(
        "list_voices", VendorError("down", vendor="elevenlabs", retryable=True), times=1
    )

    options = (await api_client.get(_url(owner), headers=owner.headers)).json()

    assert options and all(option["avatar"] == "" for option in options)


async def test_the_configured_voices_are_the_ones_put_in_front_of_a_customer(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """They are the ones this deployment has actually published with."""
    owner = await live_owner(api_client, api_app)

    options = (await api_client.get(_url(owner), headers=owner.headers)).json()

    configured = set(api_app.state.settings.voice_map.values())
    leading = [option["voice_id"] for option in options[: len(configured)]]
    assert set(leading) == configured
    assert all(option["recommended"] for option in options[:3])


async def test_a_vendor_that_is_down_still_offers_the_configured_voices(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """A signup must not stop because a catalogue read failed.

    The fallback has no names, no samples and no sentences, because none of
    that exists outside the catalogue — a shorter picker, not invented copy.
    """
    owner = await live_owner(api_client, api_app)
    api_app.state.providers.elevenlabs.behaviour.fail(
        "list_voices", VendorError("down", vendor="elevenlabs", retryable=True), times=1
    )

    response = await api_client.get(_url(owner), headers=owner.headers)

    assert response.status_code == 200, response.text
    options = response.json()
    assert {option["voice_id"] for option in options} >= set(
        api_app.state.settings.voice_map.values()
    )
    assert all(option["preview_url"] is None for option in options)


async def test_the_catalogue_is_read_once_and_then_cached() -> None:
    """The onboarding page would otherwise fetch it on every view.

    Asserted on the service against a cache double rather than through the API,
    because the suite deliberately runs without a reachable Redis — the cache
    client fails soft, so an end-to-end version of this would be testing that
    the cache is *absent*.
    """
    from app.providers.fakes.voice import FakeElevenLabsProvider
    from app.services.voice_catalogue import VoiceCatalogue

    class _Cache:
        """Just enough of the cache: a dict with the real signatures."""

        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.store.get(key)

        async def set(self, key: str, value: str, *, ttl_s: int | None = None) -> None:
            del ttl_s
            self.store[key] = value

    voice = FakeElevenLabsProvider()
    cache = _Cache()
    catalogue = VoiceCatalogue(build_settings(), voice, cache)  # type: ignore[arg-type]

    first = await catalogue.options()
    second = await catalogue.options()

    assert [option.voice_id for option in first] == [option.voice_id for option in second]
    assert sum(1 for name, _ in voice.behaviour.calls if name == "list_voices") == 1


async def test_a_cached_catalogue_that_cannot_be_read_is_fetched_again() -> None:
    """A cache holding something from an older release must not empty the picker."""
    from app.providers.fakes.voice import FakeElevenLabsProvider
    from app.services.voice_catalogue import CACHE_KEY, VoiceCatalogue

    class _Cache:
        def __init__(self) -> None:
            self.store: dict[str, str] = {CACHE_KEY: "not json at all"}

        async def get(self, key: str) -> str | None:
            return self.store.get(key)

        async def set(self, key: str, value: str, *, ttl_s: int | None = None) -> None:
            del ttl_s
            self.store[key] = value

    catalogue = VoiceCatalogue(build_settings(), FakeElevenLabsProvider(), _Cache())  # type: ignore[arg-type]

    assert await catalogue.options(), "an unreadable cache entry is discarded, not served"


async def test_reading_the_voices_needs_a_membership(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    owner = await live_owner(api_client, api_app)
    member = await add_member(api_app, owner.tenant_id, MembershipRole.MEMBER)

    assert (await api_client.get(_url(owner))).status_code == 401
    assert (await api_client.get(_url(owner), headers=member)).status_code == 200


# ---------------------------------------------------------------------------
# Changing it later, which setup promises
# ---------------------------------------------------------------------------


async def test_the_voice_can_be_changed_after_setup(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """ "You can change this later" is printed on the signup screen."""
    owner = await live_owner(api_client, api_app)

    response = await _save(api_client, owner, voice_id=CATALOGUE_ONLY)

    assert response.status_code == 200, response.text
    assert (await _live_config(api_app, owner.tenant_id)).voice_id == CATALOGUE_ONLY


async def test_a_voice_outside_the_catalogue_is_refused_with_the_field_named(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """Refused at the one moment the customer can still be told which field."""
    owner = await live_owner(api_client, api_app)

    response = await _save(api_client, owner, voice_id="not-a-voice-we-have")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["field"] == "voice_id"


async def test_a_chosen_voice_survives_an_unrelated_edit(
    api_client: AsyncClient, api_app: FastAPI
) -> None:
    """The regression this feature would otherwise have shipped.

    Publishing used to re-check the stored voice against the three configured
    style defaults and fall back when it was not one of them — so a business
    that chose a catalogue voice would have been reset to the style voice the
    next time they touched their opening hours, with nothing said about it.
    """
    owner = await live_owner(api_client, api_app)
    await _save(api_client, owner, voice_id=CATALOGUE_ONLY)

    response = await _save(api_client, owner, services="cuts, color, blowouts, braids")

    assert response.status_code == 200, response.text
    assert (await _live_config(api_app, owner.tenant_id)).voice_id == CATALOGUE_ONLY


async def test_an_unchanged_voice_is_not_an_edit(api_client: AsyncClient, api_app: FastAPI) -> None:
    """Saving the voice it already has must not burn a version."""
    owner = await live_owner(api_client, api_app)
    await _save(api_client, owner, voice_id=CATALOGUE_ONLY)
    version = (await _live_config(api_app, owner.tenant_id)).version

    response = await _save(api_client, owner, voice_id=CATALOGUE_ONLY)

    assert response.status_code == 200, response.text
    assert (await _live_config(api_app, owner.tenant_id)).version == version


async def test_a_member_cannot_change_the_voice(api_client: AsyncClient, api_app: FastAPI) -> None:
    owner = await live_owner(api_client, api_app)
    member = await add_member(api_app, owner.tenant_id, MembershipRole.MEMBER)

    response = await api_client.put(
        f"/api/v1/tenants/{owner.tenant_id}/agent/settings",
        json={**SETTINGS, "voice_id": CATALOGUE_ONLY},
        headers=member,
    )

    assert response.status_code == 403, response.text
