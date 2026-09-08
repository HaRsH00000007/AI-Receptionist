"""In-memory Twilio.

Reproduces the behaviour the purchase step actually depends on: an area code
may have no inventory, a number can only be sold once, and a number bought under
a friendly name can be found again. That last one is what the adoption guard
relies on, so it has to be real here or the guard is untested.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from app.core.errors import VendorError
from app.data.area_codes import area_codes_for_region
from app.providers.fakes.support import Behaviour
from app.providers.models import AvailableNumber, PurchasedNumber

#: Area codes the fake pretends to have inventory in. Anything else searches
#: empty, which is how the fallback ladder gets exercised.
STOCKED_AREA_CODES: frozenset[str] = frozenset(
    {"805", "213", "310", "415", "628", "312", "773", "212", "718", "617", "512", "737"}
)

#: Deliberately out of stock, so a test can force the ladder all the way down.
EXHAUSTED_AREA_CODES: frozenset[str] = frozenset({"999"})


@dataclass
class FakeTwilioProvider:
    """A Twilio that never charges anyone."""

    name: str = "fake-twilio"
    behaviour: Behaviour = field(default_factory=Behaviour)
    purchased: dict[str, PurchasedNumber] = field(default_factory=dict)
    released: set[str] = field(default_factory=set)
    _sid_counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    # ---- search ----------------------------------------------------------
    async def search_available_numbers(
        self,
        *,
        area_code: str | None = None,
        in_region: str | None = None,
        toll_free: bool = False,
        limit: int = 5,
    ) -> list[AvailableNumber]:
        self.behaviour.record(
            "search_available_numbers",
            area_code=area_code,
            in_region=in_region,
            toll_free=toll_free,
        )

        if toll_free:
            candidates = ["833", "844", "855"]
        elif area_code is not None:
            candidates = [area_code] if area_code in STOCKED_AREA_CODES else []
        elif in_region is not None:
            candidates = [
                code for code in area_codes_for_region(in_region) if code in STOCKED_AREA_CODES
            ]
        else:
            candidates = sorted(STOCKED_AREA_CODES)

        candidates = [code for code in candidates if code not in EXHAUSTED_AREA_CODES]

        offers: list[AvailableNumber] = []
        for code in candidates:
            for suffix in range(limit):
                e164 = f"+1{code}555{1000 + suffix:04d}"
                if e164 in self.purchased:
                    continue
                offers.append(
                    AvailableNumber(
                        e164=e164,
                        locality="Testville",
                        region=in_region or "CA",
                        voice_enabled=True,
                    )
                )
                if len(offers) >= limit:
                    return offers
        return offers

    # ---- ownership -------------------------------------------------------
    async def purchase_number(self, *, e164: str, friendly_name: str) -> PurchasedNumber:
        self.behaviour.record("purchase_number", e164=e164, friendly_name=friendly_name)

        if e164 in self.purchased:
            # Twilio answers 21422 for a number that is no longer for sale.
            raise VendorError(
                "number is no longer available",
                vendor=self.name,
                status_code=400,
                retryable=False,
                code="number_unavailable",
                details={"e164": e164},
            )

        number = PurchasedNumber(
            sid=f"PN{next(self._sid_counter):032d}",
            e164=e164,
            friendly_name=friendly_name,
        )
        self.purchased[e164] = number
        return number

    async def get_number(self, *, sid: str) -> PurchasedNumber | None:
        self.behaviour.record("get_number", sid=sid)
        for number in self.purchased.values():
            if number.sid == sid:
                return number
        return None

    async def find_by_friendly_name(self, *, friendly_name: str) -> PurchasedNumber | None:
        self.behaviour.record("find_by_friendly_name", friendly_name=friendly_name)
        for number in self.purchased.values():
            if number.friendly_name == friendly_name:
                return number
        return None

    async def release_number(self, *, sid: str) -> None:
        self.behaviour.record("release_number", sid=sid)
        for e164, number in list(self.purchased.items()):
            if number.sid == sid:
                del self.purchased[e164]
                self.released.add(sid)
                return
        # Releasing an unknown SID is a no-op, exactly as it is at Twilio: the
        # compensation path must be safe to run twice.
