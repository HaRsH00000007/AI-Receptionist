"""Available phone numbers.

Public, because the signup form calls it before anyone has an account. That
makes two properties load-bearing:

* **It spends nothing.** Searching is free at the vendor and reserves nothing,
  so the worst a flood of requests can do is cost us API calls.
* **It is rate limited on its own budget.** Sharing the signup limiter's budget
  would let someone lock themselves out of signing up by looking at numbers,
  which is the opposite of the point.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.deps import ProvidersDep
from app.core.logging import get_logger
from app.schemas.numbers import AvailableNumberView, NumberSearchView
from app.services.normalization import normalize_area_code
from app.services.number_search import NumberSearchService

logger = get_logger(__name__)

router = APIRouter(prefix="/numbers", tags=["numbers"])


def _client_key(request: Request) -> str:
    """Rate-limit key. Honours one proxy hop, which is what Railway/Fly add."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get(
    "/available",
    response_model=NumberSearchView,
    summary="Numbers a business can choose from",
)
async def available_numbers(
    request: Request,
    providers: ProvidersDep,
    area_code: str = Query(min_length=3, max_length=10),
) -> NumberSearchView:
    """Numbers in the requested area code, or nearby ones when it has none.

    An unknown area code is refused here rather than searched: the vendor would
    simply return nothing, and "212 has no numbers" reads very differently from
    "21 is not an area code".
    """
    await request.app.state.signup_rate_limiter.check(f"numbers:{_client_key(request)}")

    code = normalize_area_code(area_code)
    result = await NumberSearchService(providers.twilio).search(code)

    return NumberSearchView(
        requested_area_code=result.requested_area_code,
        exact_match=result.exact_match,
        strategy=result.strategy,
        numbers=[
            AvailableNumberView(
                e164=offer.e164,
                area_code=offer.area_code,
                locality=offer.locality,
                region=offer.region,
            )
            for offer in result.offers
        ],
    )
