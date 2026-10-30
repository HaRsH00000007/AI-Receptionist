"""Deciding which labels describe a call, and refusing everything else.

The classifier is the only place in this product where a model's output selects
**rows in a multi-tenant table**, so these tests are almost entirely about what
happens when the reply is wrong: invented ids, another business's id, prose
instead of JSON, a name where an id belongs, a hundred entries. In every case
the answer is the same and is asserted rather than assumed — no tags, no
exception, nothing written.

The happy path is here too, and so is the one that matters commercially: a
business with no tags makes **no model call at all**.
"""

from __future__ import annotations

import json
import uuid

from app.models import CallTag
from app.models.enums import TagColor
from app.providers.fakes.llm import FakeLLMProvider
from app.services.tag_classifier import MAX_RETURNED_IDS, TagClassifier
from tests.support import build_settings

TRANSCRIPT = "Caller: how much does a lesson cost? Agent: fifty dollars an hour."


def _tag(name: str, description: str = "when the caller asks about it") -> CallTag:
    tag = CallTag(
        tenant_id=uuid.uuid4(),
        name=name,
        description=description,
        color=TagColor.SLATE,
        enabled=True,
        position=1,
    )
    # The id is assigned at construction by UUIDPrimaryKeyMixin, which is what
    # lets this run with no database at all.
    assert tag.id is not None
    return tag


def _classifier(*scripted: str) -> tuple[TagClassifier, FakeLLMProvider]:
    llm = FakeLLMProvider(scripted=list(scripted))
    return TagClassifier(llm, build_settings()), llm


async def _classify(
    classifier: TagClassifier, tags: list[CallTag], transcript: str = TRANSCRIPT
) -> list[uuid.UUID]:
    return await classifier.classify(
        tags=tags,
        transcript=transcript,
        summary="The caller asked what a lesson costs.",
        business_name="Harsh Table Tennis Academy",
        business_type="other",
    )


def _calls(llm: FakeLLMProvider) -> int:
    return sum(1 for name, _ in llm.behaviour.calls if name == "complete")


# ---------------------------------------------------------------------------
# When it should not run at all
# ---------------------------------------------------------------------------


async def test_a_business_with_no_tags_costs_no_model_call() -> None:
    """The commercial property: nobody pays for a feature they do not use."""
    classifier, llm = _classifier()

    assert await _classify(classifier, []) == []
    assert _calls(llm) == 0


async def test_an_empty_transcript_costs_no_model_call() -> None:
    """There is nothing to classify, and it will still be empty on retry."""
    classifier, llm = _classifier()

    assert await _classify(classifier, [_tag("Pricing")], transcript="   ") == []
    assert _calls(llm) == 0


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_it_returns_the_tags_the_transcript_supports() -> None:
    pricing = _tag("Pricing")
    classifier, llm = _classifier(json.dumps({"tag_ids": [str(pricing.id)]}))

    assert await _classify(classifier, [pricing]) == [pricing.id]
    assert _calls(llm) == 1


async def test_several_tags_can_apply_to_one_call() -> None:
    pricing, sales = _tag("Pricing"), _tag("Sales inquiry")
    classifier, _ = _classifier(json.dumps({"tag_ids": [str(sales.id), str(pricing.id)]}))

    chosen = await _classify(classifier, [pricing, sales])

    # In the business's own order, not the model's, so two calls carrying the
    # same labels show them the same way round.
    assert chosen == [pricing.id, sales.id]


async def test_none_of_them_is_a_correct_answer() -> None:
    pricing = _tag("Pricing")
    classifier, _ = _classifier(json.dumps({"tag_ids": []}))

    assert await _classify(classifier, [pricing]) == []


# ---------------------------------------------------------------------------
# When the reply is wrong
# ---------------------------------------------------------------------------


async def test_a_tag_belonging_to_another_business_is_dropped() -> None:
    """The check that stands between a model's guess and a cross-tenant write."""
    mine = _tag("Pricing")
    theirs = _tag("Someone else's label")
    classifier, _ = _classifier(json.dumps({"tag_ids": [str(theirs.id)]}))

    assert await _classify(classifier, [mine]) == []


async def test_an_invented_id_is_dropped() -> None:
    pricing = _tag("Pricing")
    classifier, _ = _classifier(json.dumps({"tag_ids": [str(uuid.uuid4())]}))

    assert await _classify(classifier, [pricing]) == []


async def test_a_name_where_an_id_belongs_is_dropped() -> None:
    pricing = _tag("Pricing")
    classifier, _ = _classifier(json.dumps({"tag_ids": ["Pricing"]}))

    assert await _classify(classifier, [pricing]) == []


async def test_prose_instead_of_json_applies_no_tags() -> None:
    pricing = _tag("Pricing")
    classifier, _ = _classifier("I think this call was about pricing!")

    assert await _classify(classifier, [pricing]) == []


async def test_a_reply_that_is_not_an_object_applies_no_tags() -> None:
    pricing = _tag("Pricing")
    classifier, _ = _classifier(json.dumps([str(pricing.id)]))

    assert await _classify(classifier, [pricing]) == []


async def test_a_reply_with_no_tag_ids_list_applies_no_tags() -> None:
    pricing = _tag("Pricing")
    classifier, _ = _classifier(json.dumps({"tags": [str(pricing.id)]}))

    assert await _classify(classifier, [pricing]) == []


async def test_entries_that_are_not_strings_are_dropped() -> None:
    """A nested object stringified into a lookup would be a silent miss."""
    pricing = _tag("Pricing")
    classifier, _ = _classifier(
        json.dumps({"tag_ids": [{"id": str(pricing.id)}, None, 7, [str(pricing.id)]]})
    )

    assert await _classify(classifier, [pricing]) == []


async def test_a_duplicate_id_is_applied_once() -> None:
    pricing = _tag("Pricing")
    classifier, _ = _classifier(json.dumps({"tag_ids": [str(pricing.id)] * 5}))

    assert await _classify(classifier, [pricing]) == [pricing.id]


async def test_an_absurd_number_of_ids_is_bounded() -> None:
    """A model returning hundreds of entries is malfunctioning, and parsing all
    of them to throw them away is work nobody asked for."""
    pricing = _tag("Pricing")
    flood = [str(uuid.uuid4()) for _ in range(MAX_RETURNED_IDS * 3)]
    classifier, _ = _classifier(json.dumps({"tag_ids": [*flood, str(pricing.id)]}))

    # The real id is past the cut-off, so it is not reached — which is the
    # correct outcome: nothing in that reply is trustworthy.
    assert await _classify(classifier, [pricing]) == []


async def test_whitespace_around_an_id_is_tolerated() -> None:
    """Trimmed rather than refused: a stray space is a formatting slip, not a
    different id, and the value is still matched against the allowed set."""
    pricing = _tag("Pricing")
    classifier, _ = _classifier(json.dumps({"tag_ids": [f"  {pricing.id}  "]}))

    assert await _classify(classifier, [pricing]) == [pricing.id]
