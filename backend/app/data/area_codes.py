"""US area code → state → timezone.

This table is the reason no model is ever asked "which area code is closest to
Naperville?". Area code is a field the owner typed on the form; turning it into a
state and a timezone is a lookup, not a reasoning task
(docs/00_DECISIONS.md section 3).

It has two jobs:

* derive a tenant's timezone during the validate step, because "Mon-Fri 9-6"
  means nothing without one;
* give the purchase step a same-state fallback when the requested area code has
  no inventory.

**Accuracy caveat.** Several states span two timezones (Florida, Indiana,
Kentucky, Michigan, Tennessee, Texas, Kansas, Nebraska, North and South Dakota,
Oregon, Idaho). This table records the *predominant* zone for the state, so a
tenant in the Florida panhandle or western Kentucky will be given Eastern when
they are Central. That is a known, bounded inaccuracy: the signup form should
confirm the timezone with the owner, and the value is stored on the tenant so it
can be corrected without re-deriving anything.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Predominant IANA timezone per state. Arizona is separate because it does not
#: observe daylight saving, which a generic "Mountain" would get wrong.
STATE_TIMEZONES: dict[str, str] = {
    "AK": "America/Anchorage",
    "AL": "America/Chicago",
    "AR": "America/Chicago",
    "AZ": "America/Phoenix",
    "CA": "America/Los_Angeles",
    "CO": "America/Denver",
    "CT": "America/New_York",
    "DC": "America/New_York",
    "DE": "America/New_York",
    "FL": "America/New_York",
    "GA": "America/New_York",
    "HI": "Pacific/Honolulu",
    "IA": "America/Chicago",
    "ID": "America/Boise",
    "IL": "America/Chicago",
    "IN": "America/Indiana/Indianapolis",
    "KS": "America/Chicago",
    "KY": "America/New_York",
    "LA": "America/Chicago",
    "MA": "America/New_York",
    "MD": "America/New_York",
    "ME": "America/New_York",
    "MI": "America/Detroit",
    "MN": "America/Chicago",
    "MO": "America/Chicago",
    "MS": "America/Chicago",
    "MT": "America/Denver",
    "NC": "America/New_York",
    "ND": "America/Chicago",
    "NE": "America/Chicago",
    "NH": "America/New_York",
    "NJ": "America/New_York",
    "NM": "America/Denver",
    "NV": "America/Los_Angeles",
    "NY": "America/New_York",
    "OH": "America/New_York",
    "OK": "America/Chicago",
    "OR": "America/Los_Angeles",
    "PA": "America/New_York",
    "RI": "America/New_York",
    "SC": "America/New_York",
    "SD": "America/Chicago",
    "TN": "America/Chicago",
    "TX": "America/Chicago",
    "UT": "America/Denver",
    "VA": "America/New_York",
    "VT": "America/New_York",
    "WA": "America/Los_Angeles",
    "WI": "America/Chicago",
    "WV": "America/New_York",
    "WY": "America/Denver",
}

#: State → its geographic area codes. Toll-free codes are handled separately;
#: they belong to no state, which is exactly why they sit last in the ladder.
_STATE_AREA_CODES: dict[str, tuple[str, ...]] = {
    "AL": ("205", "251", "256", "334", "659", "938"),
    "AK": ("907",),
    "AZ": ("480", "520", "602", "623", "928"),
    "AR": ("327", "479", "501", "870"),
    "CA": (
        "209",
        "213",
        "279",
        "310",
        "323",
        "341",
        "350",
        "408",
        "415",
        "424",
        "442",
        "510",
        "530",
        "559",
        "562",
        "619",
        "626",
        "628",
        "650",
        "657",
        "661",
        "669",
        "707",
        "714",
        "747",
        "760",
        "805",
        "818",
        "820",
        "831",
        "840",
        "858",
        "909",
        "916",
        "925",
        "949",
        "951",
    ),
    "CO": ("303", "719", "720", "970", "983"),
    "CT": ("203", "475", "860", "959"),
    "DE": ("302",),
    "DC": ("202",),
    "FL": (
        "239",
        "305",
        "321",
        "324",
        "352",
        "386",
        "407",
        "448",
        "561",
        "656",
        "689",
        "727",
        "728",
        "754",
        "772",
        "786",
        "813",
        "850",
        "863",
        "904",
        "941",
        "954",
    ),
    "GA": ("229", "404", "470", "478", "678", "706", "762", "770", "912", "943"),
    "HI": ("808",),
    "ID": ("208", "986"),
    "IL": (
        "217",
        "224",
        "309",
        "312",
        "331",
        "447",
        "464",
        "618",
        "630",
        "708",
        "730",
        "773",
        "779",
        "815",
        "847",
        "872",
    ),
    "IN": ("219", "260", "317", "463", "574", "765", "812", "930"),
    "IA": ("319", "515", "563", "641", "712"),
    "KS": ("316", "620", "785", "913"),
    "KY": ("270", "364", "502", "606", "859"),
    "LA": ("225", "318", "337", "504", "985"),
    "ME": ("207",),
    "MD": ("227", "240", "301", "410", "443", "667"),
    "MA": ("339", "351", "413", "508", "617", "774", "781", "857", "978"),
    "MI": (
        "231",
        "248",
        "269",
        "313",
        "517",
        "586",
        "616",
        "679",
        "734",
        "810",
        "906",
        "947",
        "989",
    ),
    "MN": ("218", "320", "507", "612", "651", "763", "952"),
    "MS": ("228", "601", "662", "769"),
    "MO": ("235", "314", "417", "557", "573", "636", "660", "816"),
    "MT": ("406",),
    "NE": ("308", "402", "531"),
    "NV": ("702", "725", "775"),
    "NH": ("603",),
    "NJ": ("201", "551", "609", "640", "732", "848", "856", "862", "908", "973"),
    "NM": ("505", "575"),
    "NY": (
        "212",
        "315",
        "329",
        "332",
        "347",
        "363",
        "516",
        "518",
        "585",
        "607",
        "631",
        "646",
        "680",
        "716",
        "718",
        "838",
        "845",
        "914",
        "917",
        "929",
        "934",
    ),
    "NC": ("252", "336", "704", "743", "828", "910", "919", "980", "984"),
    "ND": ("701",),
    "OH": (
        "216",
        "220",
        "234",
        "283",
        "326",
        "330",
        "380",
        "419",
        "436",
        "440",
        "513",
        "567",
        "614",
        "740",
        "937",
    ),
    "OK": ("405", "539", "572", "580", "918"),
    "OR": ("458", "503", "541", "971"),
    "PA": (
        "215",
        "223",
        "267",
        "272",
        "412",
        "445",
        "484",
        "570",
        "582",
        "610",
        "717",
        "724",
        "814",
        "835",
        "878",
    ),
    "RI": ("401",),
    "SC": ("803", "839", "843", "854", "864"),
    "SD": ("605",),
    "TN": ("423", "615", "629", "731", "865", "901", "931"),
    "TX": (
        "210",
        "214",
        "254",
        "281",
        "325",
        "346",
        "361",
        "409",
        "430",
        "432",
        "469",
        "512",
        "682",
        "713",
        "726",
        "737",
        "806",
        "817",
        "830",
        "832",
        "903",
        "915",
        "936",
        "940",
        "945",
        "956",
        "972",
        "979",
    ),
    "UT": ("385", "435", "801"),
    "VT": ("802",),
    "VA": ("276", "434", "540", "571", "703", "757", "804", "826", "948"),
    "WA": ("206", "253", "360", "425", "509", "564"),
    "WV": ("304", "681"),
    "WI": ("262", "274", "414", "534", "608", "715", "920"),
    "WY": ("307",),
}

#: Toll-free codes. Not tied to a state, which is why they are the last rung of
#: the fallback ladder rather than part of the regional search.
TOLL_FREE_AREA_CODES: frozenset[str] = frozenset({"800", "833", "844", "855", "866", "877", "888"})

#: Reverse index, built once at import.
AREA_CODE_STATES: dict[str, str] = {
    code: state for state, codes in _STATE_AREA_CODES.items() for code in codes
}

#: The default when an area code is unknown. Pacific, because the business this
#: replaces is US-west-coast heavy; it is a documented default, not a guess made
#: at runtime.
DEFAULT_TIMEZONE = "America/Los_Angeles"


@dataclass(frozen=True, slots=True)
class AreaCodeInfo:
    area_code: str
    state: str | None
    timezone: str
    is_toll_free: bool


def lookup(area_code: str) -> AreaCodeInfo:
    """Resolve an area code to its state and timezone.

    Never raises: an unknown code yields the documented default so that a valid
    signup is not blocked by a gap in a reference table.
    """
    if area_code in TOLL_FREE_AREA_CODES:
        return AreaCodeInfo(area_code, None, DEFAULT_TIMEZONE, is_toll_free=True)

    state = AREA_CODE_STATES.get(area_code)
    timezone = STATE_TIMEZONES.get(state or "", DEFAULT_TIMEZONE)
    return AreaCodeInfo(area_code, state, timezone, is_toll_free=False)


def is_known_area_code(area_code: str) -> bool:
    return area_code in AREA_CODE_STATES or area_code in TOLL_FREE_AREA_CODES


def area_codes_for_region(state: str) -> tuple[str, ...]:
    """Every geographic area code in a state, for the same-state fallback."""
    return _STATE_AREA_CODES.get(state.upper(), ())


def timezone_for_area_code(area_code: str) -> str:
    return lookup(area_code).timezone
