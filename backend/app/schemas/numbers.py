"""What the signup form sees when it asks for numbers.

Nothing here is a reservation: a number offered now can be sold to someone else
a second later, which is why the purchase step re-checks and falls back rather
than trusting this list.
"""

from __future__ import annotations

from pydantic import BaseModel


class AvailableNumberView(BaseModel):
    e164: str
    area_code: str
    #: The vendor's own labels, shown so "646 — New York" means something.
    locality: str | None = None
    region: str | None = None


class NumberSearchView(BaseModel):
    requested_area_code: str
    #: True when the numbers below are in the area code that was asked for.
    #: When false, they are alternatives and the form says so.
    exact_match: bool
    #: ``exact_area_code``, ``same_state`` or ``none``.
    strategy: str
    numbers: list[AvailableNumberView]
