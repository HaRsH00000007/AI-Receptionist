"""Correlation IDs.

Every unit of work — an HTTP request now, a provisioning run and a vendor call
later — carries one id, stamped on every log line it produces. This is the
primary key for the "what happened to tenant X" question that the Make.com
system could never answer (docs/00_DECISIONS.md section 4).

The id lives in a :class:`~contextvars.ContextVar` so that nothing has to thread
it through call signatures, and so it survives across ``await`` points.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_HEADER_NAME = "X-Correlation-ID"

# Inbound ids are echoed into logs and response headers, so they are constrained
# to a safe character set. Anything else is discarded and replaced.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:\-]{8,128}$")

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    """Generate a fresh correlation id."""
    return uuid.uuid4().hex


def get_correlation_id() -> str | None:
    """The id for the current context, or ``None`` outside any unit of work."""
    return _correlation_id.get()


def set_correlation_id(value: str) -> Token[str | None]:
    """Bind an id to the current context. Returns a token for :func:`reset`."""
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    """Restore whatever id was bound before the matching :func:`set`."""
    _correlation_id.reset(token)


def sanitize_correlation_id(value: str | None) -> str:
    """Accept a caller-supplied id if it is safe, otherwise mint a new one."""
    if value and _SAFE_ID.match(value):
        return value
    return new_correlation_id()


class CorrelationIdMiddleware:
    """Bind a correlation id to every HTTP request and echo it back.

    Written as raw ASGI rather than ``BaseHTTPMiddleware``: it avoids the extra
    task hop, and context variables set here are visible to the endpoint without
    caveats.
    """

    def __init__(self, app: ASGIApp, header_name: str = DEFAULT_HEADER_NAME) -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(self.header_name)
        correlation_id = sanitize_correlation_id(incoming)
        token = set_correlation_id(correlation_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[self.header_name] = correlation_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            reset_correlation_id(token)
