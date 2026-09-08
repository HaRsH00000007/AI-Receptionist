"""Shared HTTP plumbing for the real providers.

One place decides how a vendor failure becomes an :class:`AppError`, so retry
classification cannot drift between adapters. One place also decides what gets
logged, so a credential cannot leak from an adapter someone wrote in a hurry.
"""

from __future__ import annotations

import re
from types import TracebackType
from typing import Any, Self

import httpx

from app.core.errors import VendorError
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Header names whose values are never logged.
_SECRET_HEADERS = frozenset({"authorization", "xi-api-key", "x-api-key", "api-key"})

#: Account and key identifiers that appear inside vendor URLs. Twilio puts the
#: account SID in every path, and those paths end up in error details — which
#: reach the customer-facing status page through `run.last_error`. Redacting at
#: this layer covers the logs and the error envelope in one place.
_ID_IN_PATH = re.compile(r"(?:AC|SK|US|PN|MG)[0-9a-fA-F]{32}")


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: ("<redacted>" if key.lower() in _SECRET_HEADERS else value)
        for key, value in headers.items()
    }


def safe_path(path: str) -> str:
    """A request path with account identifiers removed.

    The path is diagnostic — which endpoint failed — and that survives. What
    does not survive is the account SID embedded in it, which is a credential
    identifier and has no business being shown to a salon owner.
    """
    return _ID_IN_PATH.sub(lambda match: f"{match.group(0)[:2]}<redacted>", path)


class ProviderHTTPClient:
    """A thin ``httpx`` wrapper that speaks the application's error taxonomy."""

    def __init__(
        self,
        *,
        vendor: str,
        base_url: str,
        timeout_s: float,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.vendor = vendor
        self._headers = headers or {}
        # `transport` exists so tests can assert the exact request each adapter
        # builds, and parse representative responses, without a network. It is
        # never set in production.
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            headers=self._headers,
            auth=auth,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        data: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200, 201, 202, 204),
    ) -> Any:
        """Perform a request and return decoded JSON (or ``None`` for 204).

        A timeout or connection error is retryable by definition — the request
        may not even have reached the vendor. A status code is classified by
        :func:`app.core.errors.is_retryable_status`, which is the single place
        that decides "never retry a 400".
        """
        try:
            response = await self._client.request(method, path, params=params, json=json, data=data)
        except httpx.TimeoutException as exc:
            raise VendorError(
                f"{self.vendor} request timed out",
                vendor=self.vendor,
                retryable=True,
                code="vendor_timeout",
                details={"method": method, "path": safe_path(path)},
            ) from exc
        except httpx.HTTPError as exc:
            raise VendorError(
                f"{self.vendor} request failed: {type(exc).__name__}",
                vendor=self.vendor,
                retryable=True,
                code="vendor_unreachable",
                details={"method": method, "path": safe_path(path)},
            ) from exc

        if response.status_code not in expected:
            raise VendorError.from_status(
                f"{self.vendor} returned {response.status_code}",
                vendor=self.vendor,
                status_code=response.status_code,
                details={
                    "method": method,
                    "path": safe_path(path),
                    # Bounded: enough to diagnose, never a whole payload dump.
                    "body": safe_path(response.text[:500]),
                },
            )

        logger.debug(
            "provider call",
            extra={
                "vendor": self.vendor,
                "method": method,
                "path": safe_path(path),
                "status": response.status_code,
                "headers": _safe_headers(self._headers),
            },
        )

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise VendorError(
                f"{self.vendor} returned a non-JSON body",
                vendor=self.vendor,
                retryable=False,
                code="vendor_bad_response",
                details={"method": method, "path": safe_path(path)},
            ) from exc

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, **kwargs)
