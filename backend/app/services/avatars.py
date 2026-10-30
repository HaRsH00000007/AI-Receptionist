"""Which drawn face belongs to which voice.

The faces are drawn in the client (``components/ui/AgentAvatar.tsx``); this
decides which one a voice gets, and it is decided **here** rather than there for
one reason: the vendor's labels for a voice — its gender, its age — live on this
side, and a face that did not match the voice a caller hears would be worse than
no face at all.

The buckets are wider than the labels: a male voice can be drawn from four
faces rather than one, because the point is a face that is *not jarring*
beside the voice, and a bucket of one hands the same face to every male
voice on a six-card grid.

The mapping is deliberately coarse. ElevenLabs labels a voice as male, female or
neutral and as young, middle aged or old, so that is what the buckets are; the
choice *within* a bucket is a hash of the voice id, which keeps two young
American women from being handed the same face while keeping the face stable —
one that changed between page loads would read as a bug.

An unlabelled voice falls into a mixed bucket rather than being left blank. A
missing face on one card in a grid looks broken; a plausible one does not, and
nothing downstream depends on it being right.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Keep in step with ``AGENT_AVATARS`` in the client. Not shared through an
#: endpoint because it changes when somebody draws a new face, which is a
#: deploy of both halves anyway.
FEMALE = ("ada", "iris", "nora", "vera")
FEMALE_OLD = ("edith",)
#: No beard in the young bucket, and no grey: the rest are age-neutral crops.
MALE_YOUNG = ("leo", "omar", "raj")
MALE_MATURE = ("theo", "raj", "leo", "omar")
MALE_OLD = ("walt",)
#: For a voice the vendor did not label, or labelled as neutral.
MIXED = ("nora", "leo", "omar", "raj")

#: The vendor's word for "old" on both its age scales.
_OLD = frozenset({"old", "older"})


def avatar_for_voice(*, voice_id: str, gender: str | None = None, age: str | None = None) -> str:
    """A stable face for this voice."""
    return _pick(_bucket(gender, age), voice_id)


def _bucket(gender: str | None, age: str | None) -> Sequence[str]:
    sex = (gender or "").strip().lower()
    years = (age or "").strip().lower()
    if sex == "female":
        return FEMALE_OLD if years in _OLD else FEMALE
    if sex == "male":
        if years in _OLD:
            return MALE_OLD
        return MALE_MATURE if years == "middle_aged" else MALE_YOUNG
    return MIXED


def _pick(bucket: Sequence[str], seed: str) -> str:
    """The same arithmetic the client uses for its own fallback, so a face
    derived on either side of the wire is the same face."""
    total = 0
    for character in seed:
        total = (total * 31 + ord(character)) % 100_000
    return bucket[total % len(bucket)]
