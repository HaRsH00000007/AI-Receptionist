"""The timezones a business may choose for its receptionist.

Deliberately not ``zoneinfo.available_timezones()``. That set is ~600 entries
including ``Etc/GMT+7`` and a pile of historical aliases, and a value chosen
there ends up rendered into the agent's prompt ("Opening hours (timezone X)")
and used to decide whether the business is open. The list a customer picks from
should be the list the product already reasons about.

So the options are derived from :data:`app.data.area_codes.STATE_TIMEZONES` —
the same table that derives a tenant's timezone from their area code at signup.
A zone can therefore never be offered that the rest of the system would not
have produced on its own, and adding a state to that table adds its zone here
without a second edit.

The labels are the names a US business would recognise. They are not generated
from the IANA name, because "America/Los_Angeles" is not what anyone calls it
and "America/Indiana/Indianapolis" is not a sentence.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.data.area_codes import STATE_TIMEZONES

#: Human labels for the zones the product uses. Every value in
#: ``STATE_TIMEZONES`` must appear here; :func:`timezone_options` asserts it, so
#: adding a state with a new zone fails loudly rather than showing a raw IANA
#: name to a customer.
_LABELS: dict[str, str] = {
    "America/New_York": "Eastern Time",
    "America/Detroit": "Eastern Time - Michigan",
    "America/Indiana/Indianapolis": "Eastern Time - Indiana",
    "America/Chicago": "Central Time",
    "America/Denver": "Mountain Time",
    "America/Boise": "Mountain Time - Idaho",
    "America/Phoenix": "Mountain Time - Arizona (no daylight saving)",
    "America/Los_Angeles": "Pacific Time",
    "America/Anchorage": "Alaska Time",
    "Pacific/Honolulu": "Hawaii Time",
}

#: Roughly east to west, which is the order a US dropdown is expected in.
_ORDER: tuple[str, ...] = (
    "America/New_York",
    "America/Detroit",
    "America/Indiana/Indianapolis",
    "America/Chicago",
    "America/Denver",
    "America/Boise",
    "America/Phoenix",
    "America/Los_Angeles",
    "America/Anchorage",
    "Pacific/Honolulu",
)


@dataclass(frozen=True)
class TimezoneOption:
    """One selectable zone: the IANA name stored, and what to show for it."""

    timezone: str
    label: str


def supported_timezones() -> frozenset[str]:
    """Every zone a tenant is allowed to be set to.

    Used by the schema to reject anything else, so a timezone that reaches the
    prompt is always one of these.
    """
    return frozenset(STATE_TIMEZONES.values())


def timezone_options() -> tuple[TimezoneOption, ...]:
    """The choices, in the order they should be offered."""
    supported = supported_timezones()
    missing = supported - set(_LABELS)
    assert not missing, f"no label for {sorted(missing)}; add it to _LABELS"

    ordered = [zone for zone in _ORDER if zone in supported]
    # Anything supported but not placed in _ORDER still gets offered, rather
    # than silently disappearing from the dropdown.
    ordered += sorted(supported - set(ordered))
    return tuple(TimezoneOption(timezone=zone, label=_LABELS[zone]) for zone in ordered)


def is_supported(timezone: str) -> bool:
    return timezone in supported_timezones()
