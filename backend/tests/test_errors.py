"""The retryable/terminal split is the contract the provisioning engine relies on."""

from __future__ import annotations

import pytest

from app.core.errors import (
    AppError,
    ConfigurationError,
    DryRunError,
    InvalidInputError,
    NotFoundError,
    RetryableError,
    TerminalError,
    VendorError,
    is_retryable_status,
)


@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retryable(status_code: int) -> None:
    assert is_retryable_status(status_code) is True


@pytest.mark.parametrize("status_code", [200, 400, 401, 403, 404, 409, 422])
def test_client_errors_are_never_retryable(status_code: int) -> None:
    """Never retry a 400 - docs/01_PLAN_POC.md, provisioning rule 4."""
    assert is_retryable_status(status_code) is False


def test_retryable_and_terminal_carry_their_classification() -> None:
    assert RetryableError("boom").retryable is True
    assert TerminalError("boom").retryable is False


@pytest.mark.parametrize(
    "error_class", [ConfigurationError, InvalidInputError, NotFoundError, DryRunError]
)
def test_terminal_subclasses_are_terminal(error_class: type[TerminalError]) -> None:
    assert issubclass(error_class, TerminalError)
    assert error_class("boom").retryable is False


def test_error_serialises_with_details() -> None:
    error = AppError("bad thing", code="custom", details={"tenant_id": "t-1"})
    assert error.to_dict() == {
        "code": "custom",
        "message": "bad thing",
        "retryable": False,
        "details": {"tenant_id": "t-1"},
    }


def test_error_omits_empty_details() -> None:
    assert "details" not in AppError("bad thing").to_dict()


def test_vendor_error_from_status_classifies_retryability() -> None:
    transient = VendorError.from_status("rate limited", vendor="twilio", status_code=429)
    permanent = VendorError.from_status("bad request", vendor="twilio", status_code=400)

    assert transient.retryable is True
    assert permanent.retryable is False


def test_vendor_error_records_context_for_the_admin_panel() -> None:
    error = VendorError.from_status(
        "no numbers available", vendor="twilio", status_code=404, details={"area_code": "805"}
    )
    assert error.details == {"area_code": "805", "vendor": "twilio", "status_code": 404}
    assert error.vendor == "twilio"
    assert error.status_code == 404
