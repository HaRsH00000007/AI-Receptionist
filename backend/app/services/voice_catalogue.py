"""The voices a customer may choose from, and how they are described.

The catalogue is the **vendor's**, not ours. ElevenLabs holds the names, the
sentence under each one and the sample a customer listens to, and it changes
them without telling us — so this reads the account's catalogue rather than
shipping a list that would quietly go stale, and a voice the account has lost
disappears from the picker instead of failing on publish.

Three things this module is responsible for, each because the alternative is
something a customer would notice:

* **Only voices the call path can actually speak.** The agent body sends
  ``"language": "en"``; a Spanish voice offered in the picker would be chosen
  by somebody and then answer in English. Non-English voices are filtered out
  here, and the language selector offers exactly what is left.
* **A recommended few, then the rest.** Thirty voices is not a choice, it is a
  decision nobody wants to make while signing up. The configured ones come
  first — they are the ones this deployment has actually used — followed by
  conversational voices, and the picker shows a handful with the rest behind a
  toggle.
* **Never an empty picker.** A vendor that is down must not block onboarding,
  so a failed read falls back to the configured voice map: fewer options,
  plainer names, and a working form.

Cached, because the catalogue barely changes and the onboarding page would
otherwise fetch it on every view. The cache is optional: callers that have no
Redis (a publish, a test) read through to the vendor.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from app.cache.client import CacheClient
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.models import VoiceRef
from app.providers.protocols import ElevenLabsProvider
from app.schemas.website import VoiceOptionView
from app.services.avatars import avatar_for_voice

logger = get_logger(__name__)

#: One key for the whole account; the catalogue is not per tenant.
CACHE_KEY = "voices:catalogue:v1"

#: How many voices the picker shows before "show all". Six, because that is two
#: rows of three and because every extra row is a decision deferred.
RECOMMENDED = 6

#: The only language the call path sends (``app.providers.real.voice`` writes
#: ``"language": "en"`` into the agent body). Offering another would be a lie
#: told in a dropdown. When the adapter learns to send it, this grows.
SUPPORTED_LANGUAGES = ("en",)

#: Vendor ``use_case`` labels that suit answering a phone. A voice tuned for
#: audiobook narration can be chosen from the full list, but it is not put in
#: front of somebody setting up a receptionist.
_CONVERSATIONAL = frozenset({"conversational", "informative_educational", "advertisement"})


class VoiceCatalogue:
    """Reads, filters and describes the voices on offer."""

    def __init__(
        self,
        settings: Settings,
        voice: ElevenLabsProvider,
        cache: CacheClient | None = None,
    ) -> None:
        self.settings = settings
        self.voice = voice
        self.cache = cache

    async def options(self) -> list[VoiceOptionView]:
        """Every voice on offer, recommended ones first."""
        voices = await self._voices()
        if not voices:
            return self._configured_only()
        return _describe(voices, settings=self.settings)

    async def voice_ids(self) -> set[str]:
        """The ids a customer may choose. Used to refuse anything else.

        Always includes the configured ones, even when the vendor read failed:
        those are the ids this deployment publishes with by default, and a
        validation that rejected its own default would break every save.
        """
        return {option.voice_id for option in await self.options()} | _configured_ids(self.settings)

    # ---- reading ---------------------------------------------------------
    async def _voices(self) -> list[VoiceRef]:
        cached = await self._from_cache()
        if cached is not None:
            return cached
        try:
            voices = list(await self.voice.list_voices())
        except Exception:
            # Logged, not raised. The caller falls back to the configured map,
            # which is a smaller picker rather than a broken page.
            logger.exception(
                "could not read the voice catalogue; falling back to the configured voices"
            )
            return []
        await self._to_cache(voices)
        return voices

    async def _from_cache(self) -> list[VoiceRef] | None:
        if self.cache is None:
            return None
        raw = await self.cache.get(CACHE_KEY)
        if not isinstance(raw, str):
            return None
        try:
            payload = json.loads(raw)
            return [VoiceRef(**entry) for entry in payload]
        except (ValueError, TypeError):
            logger.warning("discarding an unreadable cached voice catalogue")
            return None

    async def _to_cache(self, voices: Sequence[VoiceRef]) -> None:
        if self.cache is None or not voices:
            return
        await self.cache.set(
            CACHE_KEY,
            json.dumps([voice.model_dump(mode="json") for voice in voices]),
            ttl_s=self.settings.redis_config_cache_ttl_s,
        )

    def _configured_only(self) -> list[VoiceOptionView]:
        """The fallback picker: what configuration alone can describe.

        No names, no samples and no sentences, because none of that exists
        outside the vendor's catalogue — inventing them here is exactly the
        failure this product does not want. The styles are real, so they are
        what the customer gets to choose between.
        """
        options = [
            VoiceOptionView(
                voice_id=voice_id,
                label=style.replace("_", " ").title(),
                style=style,
                summary="One of the voices this workspace is set up for.",
                recommended=True,
                # No labels to read without the catalogue, so no face is
                # claimed; the client draws one from the id instead.
                avatar="",
            )
            for style, voice_id in self.settings.voice_map.items()
        ]
        default = self.settings.elevenlabs_default_voice_id
        if default and default not in {option.voice_id for option in options}:
            options.append(
                VoiceOptionView(
                    voice_id=default,
                    label="Default",
                    style=None,
                    summary="The voice this workspace falls back to.",
                    recommended=True,
                )
            )
        return options


# ---------------------------------------------------------------------------
# Describing
# ---------------------------------------------------------------------------
def _describe(voices: Iterable[VoiceRef], *, settings: Settings) -> list[VoiceOptionView]:
    """Turn the vendor's catalogue into the picker's options, in order."""
    styles = {voice_id: style for style, voice_id in settings.voice_map.items()}
    speakable = [voice for voice in voices if _is_speakable(voice)]
    ordered = sorted(speakable, key=lambda voice: _rank(voice, styles))
    return [
        VoiceOptionView(
            voice_id=voice.voice_id,
            label=_short_name(voice.name),
            style=styles.get(voice.voice_id),
            summary=_summary(voice),
            description=voice.description,
            accent=_titled(voice.accent),
            gender=voice.gender,
            age=voice.age,
            language=voice.language or SUPPORTED_LANGUAGES[0],
            preview_url=voice.preview_url,
            recommended=index < RECOMMENDED,
            avatar=avatar_for_voice(voice_id=voice.voice_id, gender=voice.gender, age=voice.age),
        )
        for index, voice in enumerate(ordered)
    ]


def _is_speakable(voice: VoiceRef) -> bool:
    """Can the call path actually answer the phone in this voice?

    An unlabelled language is treated as English rather than dropped: the
    vendor leaves the label off some voices, and losing them would thin the
    picker for no reason the customer could see.
    """
    return (voice.language or SUPPORTED_LANGUAGES[0]) in SUPPORTED_LANGUAGES


def _rank(voice: VoiceRef, styles: dict[str, str]) -> tuple[int, str]:
    """Sort key: configured first, then conversational, then the rest.

    The second element is the display name, so the order within a band is
    stable between reads — a picker that reshuffles itself on refresh makes a
    customer think they lost their choice.
    """
    if voice.voice_id in styles:
        band = 0
    elif (voice.use_case or "") in _CONVERSATIONAL:
        band = 1
    else:
        band = 2
    return band, _short_name(voice.name).lower()


def _short_name(name: str) -> str:
    """ "Sarah - Mature, Reassuring, Confident" is shown as "Sarah".

    The rest of the vendor's name is a tagline, and it belongs in the line
    underneath rather than in a heading that has to fit on a card.
    """
    return (name.split(" - ", 1)[0] or name).strip() or name


def _tagline(name: str) -> str | None:
    """The part of the vendor's name after the dash, when there is one."""
    _, _, rest = name.partition(" - ")
    return rest.strip() or None


def _summary(voice: VoiceRef) -> str:
    """The line under the name: "American · Warm and friendly female voice".

    Built from the vendor's own labels and tagline, so it describes the voice a
    caller will hear rather than how we would like it to sound. Degrades a
    clause at a time: a voice with no labels at all still gets its name.
    """
    parts = [part for part in (_titled(voice.accent), _tagline(voice.name)) if part]
    if len(parts) < 2 and voice.description:
        # One clause and a real sentence available: use the sentence, trimmed
        # to the first phrase, rather than padding with label soup.
        parts = [part for part in (_titled(voice.accent), _first_clause(voice.description)) if part]
    return " · ".join(parts) if parts else "A voice from your workspace's catalogue."


def _first_clause(description: str) -> str:
    """The first sentence of a vendor description, without its full stop."""
    head = description.strip().split(".", 1)[0].strip()
    return head or description.strip()


def _titled(label: str | None) -> str | None:
    return label.replace("_", " ").title() if label else None


def _configured_ids(settings: Settings) -> set[str]:
    ids = {*settings.voice_map.values()}
    if settings.elevenlabs_default_voice_id:
        ids.add(settings.elevenlabs_default_voice_id)
    return ids


def dumped(options: Sequence[VoiceOptionView]) -> list[dict[str, Any]]:
    """Options as plain JSON, for a response that is not a FastAPI model."""
    return [option.model_dump(mode="json") for option in options]
