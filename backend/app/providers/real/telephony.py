"""Twilio, over its REST API.

The official SDK is synchronous, and wrapping it in a thread pool for every call
would put a blocking hop in the middle of an otherwise async worker. The three
endpoints this POC needs are simple form-encoded calls, so they are made
directly.

**Not tested against the live API.** The search/purchase/release shapes follow
Twilio's documented 2010-04-01 REST interface; the fake provider is what CI
exercises. See the README for how to point this at a real trial account.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.errors import VendorError
from app.providers.http import ProviderHTTPClient
from app.providers.models import AvailableNumber, PurchasedNumber

_API_VERSION = "2010-04-01"


class TwilioRestProvider:
    """Number search, purchase and release."""

    name = "twilio"

    def __init__(self, settings: Settings) -> None:
        self._account_sid = settings.twilio_account_sid
        self._client = ProviderHTTPClient(
            vendor="twilio",
            base_url=settings.twilio_api_base_url,
            timeout_s=settings.provider_timeout_s,
            auth=(settings.twilio_account_sid, settings.twilio_auth_token.get_secret_value()),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def _base(self) -> str:
        return f"/{_API_VERSION}/Accounts/{self._account_sid}"

    # ---- search ----------------------------------------------------------
    async def search_available_numbers(
        self,
        *,
        area_code: str | None = None,
        in_region: str | None = None,
        toll_free: bool = False,
        limit: int = 5,
    ) -> list[AvailableNumber]:
        kind = "TollFree" if toll_free else "Local"
        params: dict[str, Any] = {"VoiceEnabled": "true", "PageSize": limit}
        if area_code:
            params["AreaCode"] = area_code
        if in_region:
            params["InRegion"] = in_region

        payload = await self._client.get(
            f"{self._base}/AvailablePhoneNumbers/US/{kind}.json", params=params
        )
        entries = payload.get("available_phone_numbers", []) if payload else []
        return [
            AvailableNumber(
                e164=entry["phone_number"],
                locality=entry.get("locality"),
                region=entry.get("region"),
                iso_country=entry.get("iso_country", "US"),
                voice_enabled=bool(entry.get("capabilities", {}).get("voice", True)),
            )
            for entry in entries
        ]

    # ---- ownership -------------------------------------------------------
    async def purchase_number(self, *, e164: str, friendly_name: str) -> PurchasedNumber:
        payload = await self._client.post(
            f"{self._base}/IncomingPhoneNumbers.json",
            data={"PhoneNumber": e164, "FriendlyName": friendly_name},
        )
        if not payload or "sid" not in payload:
            raise VendorError(
                "twilio purchase returned no SID",
                vendor=self.name,
                retryable=False,
                code="vendor_bad_response",
            )
        return PurchasedNumber(
            sid=payload["sid"],
            e164=payload.get("phone_number", e164),
            friendly_name=payload.get("friendly_name"),
        )

    async def get_number(self, *, sid: str) -> PurchasedNumber | None:
        try:
            payload = await self._client.get(f"{self._base}/IncomingPhoneNumbers/{sid}.json")
        except VendorError as exc:
            if exc.status_code == 404:
                return None
            raise
        return PurchasedNumber(
            sid=payload["sid"],
            e164=payload["phone_number"],
            friendly_name=payload.get("friendly_name"),
        )

    async def find_by_friendly_name(self, *, friendly_name: str) -> PurchasedNumber | None:
        """The adoption guard, asked of Twilio rather than of our database.

        Our row may be missing precisely because the worker died before it could
        be written, so the authoritative answer has to come from the vendor.
        """
        payload = await self._client.get(
            f"{self._base}/IncomingPhoneNumbers.json",
            params={"FriendlyName": friendly_name, "PageSize": 1},
        )
        entries = payload.get("incoming_phone_numbers", []) if payload else []
        if not entries:
            return None
        entry = entries[0]
        return PurchasedNumber(
            sid=entry["sid"],
            e164=entry["phone_number"],
            friendly_name=entry.get("friendly_name"),
        )

    async def release_number(self, *, sid: str) -> None:
        try:
            await self._client.delete(
                f"{self._base}/IncomingPhoneNumbers/{sid}.json", expected=(204, 200)
            )
        except VendorError as exc:
            # Already gone is the desired end state; compensation must be safe
            # to run twice.
            if exc.status_code == 404:
                return
            raise
