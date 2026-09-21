"""Finding the numbers a business can choose from.

Search and purchase are deliberately separate, the way the vendor separates
them: nothing in this module spends money or reserves anything, so it is safe
to call from the public signup form as often as someone retypes an area code.

Two rules shape the result.

**Alternatives are geographic, never numeric.** Area codes are not ordered by
distance — 212 is New York and 213 is Los Angeles — so a "nearby" number is
found by looking up which state the requested code belongs to and asking the
vendor for that region. No arithmetic on the digits, and no model.

**Alternatives appear only when the requested code has nothing.** A customer
who asks for 212 and can have 212 is never shown anything else; the fallback is
fallback, not part of every search.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger
from app.data.area_codes import lookup
from app.providers.models import AvailableNumber
from app.providers.protocols import TwilioProvider
from app.services.normalization import area_code_of

logger = get_logger(__name__)

#: How many numbers to offer. Enough to feel like a choice, few enough to read.
DEFAULT_LIMIT = 6

#: Pulled before spreading across area codes, so the alternatives are not six
#: numbers from one exchange.
_NEARBY_POOL = 20

#: What produced the offers. Recorded so support can tell "there was nothing in
#: 212" from "they picked 646 for their own reasons".
STRATEGY_EXACT = "exact_area_code"
STRATEGY_SAME_STATE = "same_state"
STRATEGY_NONE = "none"


@dataclass(frozen=True, slots=True)
class NumberOffer:
    """One number on offer. Not reserved, and not ours until it is bought."""

    e164: str
    area_code: str
    locality: str | None
    region: str | None


@dataclass(frozen=True, slots=True)
class NumberSearchResult:
    requested_area_code: str
    #: True when the offers are in the area code that was actually asked for.
    exact_match: bool
    strategy: str
    offers: list[NumberOffer]


def _offer(number: AvailableNumber) -> NumberOffer:
    return NumberOffer(
        e164=number.e164,
        area_code=area_code_of(number.e164),
        locality=number.locality,
        region=number.region,
    )


def _usable(numbers: list[AvailableNumber]) -> list[AvailableNumber]:
    """A number that cannot take a call is no use to a receptionist."""
    return [number for number in numbers if number.voice_enabled]


def _spread(numbers: list[AvailableNumber], limit: int) -> list[AvailableNumber]:
    """Round-robin across area codes, so alternatives show real variety."""
    buckets: dict[str, list[AvailableNumber]] = {}
    for number in numbers:
        buckets.setdefault(area_code_of(number.e164), []).append(number)

    spread: list[AvailableNumber] = []
    while len(spread) < limit and any(buckets.values()):
        for bucket in buckets.values():
            if not bucket:
                continue
            spread.append(bucket.pop(0))
            if len(spread) >= limit:
                break
    return spread


class NumberSearchService:
    """Looks; never buys."""

    def __init__(self, twilio: TwilioProvider, *, limit: int = DEFAULT_LIMIT) -> None:
        self.twilio = twilio
        self.limit = limit

    async def search(self, area_code: str) -> NumberSearchResult:
        exact = _usable(
            await self.twilio.search_available_numbers(area_code=area_code, limit=self.limit)
        )
        if exact:
            return NumberSearchResult(
                requested_area_code=area_code,
                exact_match=True,
                strategy=STRATEGY_EXACT,
                offers=[_offer(number) for number in exact[: self.limit]],
            )

        # Only from here on. The requested area code has no inventory.
        state = lookup(area_code).state
        if state:
            nearby = _usable(
                await self.twilio.search_available_numbers(in_region=state, limit=_NEARBY_POOL)
            )
            # A number in the requested code would have shown up above; if the
            # vendor returns one anyway, it is not an *alternative*.
            alternatives = [number for number in nearby if area_code_of(number.e164) != area_code]
            if alternatives:
                logger.info(
                    "no numbers in the requested area code; offering nearby ones",
                    extra={"area_code": area_code, "state": state},
                )
                return NumberSearchResult(
                    requested_area_code=area_code,
                    exact_match=False,
                    strategy=STRATEGY_SAME_STATE,
                    offers=[_offer(number) for number in _spread(alternatives, self.limit)],
                )

        logger.info(
            "no numbers available near the requested area code",
            extra={"area_code": area_code, "state": state},
        )
        return NumberSearchResult(
            requested_area_code=area_code,
            exact_match=False,
            strategy=STRATEGY_NONE,
            offers=[],
        )
