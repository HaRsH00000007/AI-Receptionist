"""Error taxonomy.

The provisioning engine (Module 4) must decide, for every failure, whether to
back off and try again or to stop and surface the error. That decision has to be
carried by the exception itself — inspecting vendor exception classes at the
retry site is how retry logic rots. So the split is defined here, at the base of
the stack, before any vendor code exists:

* :class:`RetryableError`  — transient. Back off and try again.
* :class:`TerminalError`   — will fail identically forever. Fail the run.

The rule from docs/01_PLAN_POC.md is "never retry a 400": a validation error
does not improve on the fifth attempt, and retrying it burns the attempt budget
that a genuine timeout needs.
"""

from __future__ import annotations

from typing import Any

# Statuses that mean "the request was fine, the server was not".
# 409 is deliberately absent: a conflict is a real disagreement about state,
# not a blip, and retrying it hides the bug.
_RETRYABLE_STATUSES: frozenset[int] = frozenset({408, 425, 429})


def is_retryable_status(status_code: int) -> bool:
    """Whether an HTTP status from a vendor warrants another attempt."""
    return status_code in _RETRYABLE_STATUSES or status_code >= 500


class AppError(Exception):
    """Base for every error this application raises deliberately.

    ``details`` is structured context for the log line and the admin panel —
    never a formatted string, so it stays queryable.
    """

    retryable: bool = False
    code: str = "app_error"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        return payload

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class RetryableError(AppError):
    """Transient failure. The same call may succeed later."""

    retryable = True
    code = "retryable_error"
    http_status = 503


class TerminalError(AppError):
    """Permanent failure. Retrying changes nothing."""

    retryable = False
    code = "terminal_error"
    http_status = 400


class ConfigurationError(TerminalError):
    """The process is misconfigured — a missing key, a malformed URL."""

    code = "configuration_error"
    http_status = 500


class InvalidInputError(TerminalError):
    """Caller-supplied data failed validation."""

    code = "invalid_input"
    http_status = 422


class NotFoundError(TerminalError):
    """A referenced resource does not exist."""

    code = "not_found"
    http_status = 404


class AuthenticationError(TerminalError):
    """No valid credential was presented, or it has expired.

    401, not 403: the caller may retry *with* a credential. Deliberately vague
    in its message — "invalid or expired" rather than "no such user" or "wrong
    token" — because distinguishing them tells an attacker which half to keep
    working on, and confirms whether an email address has an account.
    """

    code = "unauthenticated"
    http_status = 401


class AuthorizationError(TerminalError):
    """A valid credential that does not permit this action.

    403. Used only where the caller is already known to be able to see that the
    resource exists. When revealing existence is itself a leak — another
    tenant's row — the correct answer is :class:`NotFoundError`, so that probing
    for ids cannot distinguish "not yours" from "not there".
    """

    code = "forbidden"
    http_status = 403


class DryRunError(TerminalError):
    """A real side effect was attempted while ``DRY_RUN`` is enabled."""

    code = "dry_run_blocked"
    http_status = 409


class BillingBlockedError(AppError):
    """Provisioning stopped because the tenant is not entitled to it.

    Deliberately neither :class:`RetryableError` nor :class:`TerminalError`.

    Not terminal, because nothing is *broken*: the tenant simply has not paid,
    and the moment they do the run should continue. Marking it terminal would
    fail the run, trigger saga compensation, and release a number the customer
    is about to be entitled to.

    Not retryable in the ordinary sense either, because backing off and trying
    again in five seconds will not make a card appear. The engine handles this
    error specially — it *parks* the run in
    :attr:`~app.models.enums.ProvisioningStatus.BILLING_BLOCKED` rather than
    failing it, and a Stripe webhook granting entitlement un-parks it.

    ``retryable`` is False so that no generic handler retries it into the
    attempt budget that a genuine vendor timeout needs.
    """

    retryable = False
    code = "billing_blocked"
    #: 402, not 403. The caller is authenticated and permitted; what is missing
    #: is payment, and a client can act on that distinction.
    http_status = 402


class VendorError(AppError):
    """A call to Twilio / ElevenLabs / an LLM / an email provider failed.

    Construct with :meth:`from_status` wherever an HTTP status is available so
    that retry classification happens in exactly one place.
    """

    code = "vendor_error"
    http_status = 502

    def __init__(
        self,
        message: str,
        *,
        vendor: str,
        status_code: int | None = None,
        retryable: bool = False,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)
        self.vendor = vendor
        self.status_code = status_code
        self.retryable = retryable
        self.details.setdefault("vendor", vendor)
        if status_code is not None:
            self.details.setdefault("status_code", status_code)

    @classmethod
    def from_status(
        cls,
        message: str,
        *,
        vendor: str,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> VendorError:
        """Build an error whose retryability is derived from the HTTP status."""
        return cls(
            message,
            vendor=vendor,
            status_code=status_code,
            retryable=is_retryable_status(status_code),
            details=details,
        )
