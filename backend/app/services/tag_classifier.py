"""Deciding which of a business's labels describe one call.

The third and last place an LLM appears, and the cheapest: same small model as
the summary, one call, a short reply. It runs only for a business that has
configured tags, so a deployment where nobody uses the feature makes no extra
model call at all.

**Nothing the model returns is trusted.** It is asked for ids and given ids, and
every id that comes back is matched against the tags *that tenant* has enabled
right now. Anything else — an invented id, a tag belonging to another business,
a name instead of an id, a duplicate, a nested object, a hundred entries — is
dropped. A model is a hint about what a call was about; it is not an
authorization decision, and the thing it is naming is a row in a multi-tenant
table.

**Failure here is never fatal.** The caller logs and continues: a call record,
its summary and its notification are obligations, and a label is a convenience.
That is the same rule contacts and metering already follow.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import CallTag
from app.prompts.loader import load_prompt, split_sections
from app.providers.protocols import LLMProvider
from app.services.config_generator import extract_json

logger = get_logger(__name__)

#: The prompt this classifier runs. Versioned like the others.
CALL_TAGS_PROMPT = "call_tags.v1"

#: Same bound as the summarizer, for the same reason: one very long call must
#: not blow the context window or the bill.
MAX_TRANSCRIPT_CHARS = 20_000

#: A ceiling on how many ids are even considered, before they are validated.
#: A model that returns two hundred entries is malfunctioning, and parsing all
#: of them to throw them away is work nobody asked for.
MAX_RETURNED_IDS = 100


class TagClassifier:
    def __init__(self, llm: LLMProvider, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    async def classify(
        self,
        *,
        tags: Sequence[CallTag],
        transcript: str,
        summary: str | None,
        business_name: str,
        business_type: str,
    ) -> list[uuid.UUID]:
        """The ids of the tags that apply, in the order the business lists them.

        Returns an empty list for "none of them", which is a correct and common
        answer — and the same thing it returns when the reply could not be read.
        The caller cannot tell those apart, and deliberately does not need to:
        both mean "write no assignments for this call".
        """
        if not tags or not transcript.strip():
            return []

        allowed = {str(tag.id): tag.id for tag in tags}
        system, user_template = split_sections(load_prompt(CALL_TAGS_PROMPT))
        user = user_template.format(
            business_name=business_name,
            business_type=business_type,
            tags=_describe(tags),
            summary=(summary or "none").strip(),
            transcript=transcript[:MAX_TRANSCRIPT_CHARS],
        )

        reply = await self.llm.complete(
            system=system,
            user=user,
            model=self.settings.llm_summary_model,
            max_tokens=256,
            temperature=self.settings.llm_temperature,
        )

        chosen = _read_ids(reply.text, allowed)
        # Returned in the business's own order rather than the model's, so two
        # calls with the same labels show them the same way round.
        ordered = [tag.id for tag in tags if tag.id in chosen]
        if len(ordered) != len(chosen):  # pragma: no cover - defensive
            logger.warning("discarded a tag id that validated but is not in the tag list")
        return ordered


def _describe(tags: Sequence[CallTag]) -> str:
    """The labels, as the prompt sees them: an id and the rule for using it."""
    return "\n".join(f"- {tag.id}: {tag.name} — {tag.description}" for tag in tags)


def _read_ids(reply: str, allowed: dict[str, uuid.UUID]) -> set[uuid.UUID]:
    """Every valid, permitted id in the reply. Everything else dropped.

    Written as a filter rather than a parse-and-raise because there is nothing
    useful to do with a bad reply: retrying would cost another model call for a
    label, and raising would put a convenience in the path of a call record.
    """
    try:
        payload: Any = extract_json(reply)
    except ValueError:
        logger.warning("tag classifier reply was not JSON; applying no tags")
        return set()

    if not isinstance(payload, dict):
        logger.warning("tag classifier reply was not an object; applying no tags")
        return set()

    raw = payload.get("tag_ids")
    if not isinstance(raw, list):
        logger.warning("tag classifier reply had no tag_ids list; applying no tags")
        return set()

    chosen: set[uuid.UUID] = set()
    dropped = 0
    for entry in raw[:MAX_RETURNED_IDS]:
        # `str` only: a dict or a list here is a malformed reply, and
        # stringifying it would turn nonsense into a lookup miss rather than
        # into the warning it deserves.
        if not isinstance(entry, str):
            dropped += 1
            continue
        found = allowed.get(entry.strip())
        if found is None:
            dropped += 1
            continue
        chosen.add(found)

    if dropped:
        # At WARNING because the interesting case is the model naming a tag that
        # belongs to somebody else, which this is the thing that stops.
        logger.warning(
            "dropped tag ids the classifier returned that this business does not have",
            extra={"dropped": dropped, "kept": len(chosen)},
        )
    return chosen
