"""The provisioning state machine, as declared by the enums.

No database involved: this is the ordering that the worker, the admin panel and
the partial unique index all read from, so it is worth pinning on its own.
"""

from __future__ import annotations

import pytest

from app.models.enums import (
    IN_FLIGHT_RUN_STATUSES,
    STATUS_AFTER_STEP,
    STEP_SEQUENCE,
    TERMINAL_RUN_STATUSES,
    ProvisioningStatus,
    ProvisioningStep,
    next_step,
    status_after,
)


def test_sequence_matches_the_plan() -> None:
    """The order in docs/01_PLAN_POC.md, spelled out so a reorder is deliberate."""
    assert STEP_SEQUENCE == (
        ProvisioningStep.VALIDATE,
        ProvisioningStep.GENERATE_CONFIG,
        ProvisioningStep.PURCHASE_NUMBER,
        ProvisioningStep.CREATE_AGENT,
        ProvisioningStep.LINK_NUMBER,
        ProvisioningStep.VERIFY,
        ProvisioningStep.ACTIVATE,
    )


def test_every_step_is_in_the_sequence_exactly_once() -> None:
    """A step defined but never scheduled would hang every run silently."""
    assert sorted(STEP_SEQUENCE) == sorted(ProvisioningStep)
    assert len(set(STEP_SEQUENCE)) == len(STEP_SEQUENCE)


def test_every_step_has_a_resulting_status() -> None:
    assert set(STATUS_AFTER_STEP) == set(ProvisioningStep)


def test_statuses_after_steps_are_distinct() -> None:
    """Two steps sharing a status would make a resumed run ambiguous."""
    statuses = list(STATUS_AFTER_STEP.values())
    assert len(set(statuses)) == len(statuses)


def test_the_run_walks_from_first_step_to_active() -> None:
    step: ProvisioningStep | None = next_step(None)
    visited: list[ProvisioningStep] = []
    while step is not None:
        visited.append(step)
        step = next_step(step)

    assert tuple(visited) == STEP_SEQUENCE
    assert status_after(visited[-1]) is ProvisioningStatus.ACTIVE


def test_first_step_is_validate() -> None:
    assert next_step(None) is ProvisioningStep.VALIDATE


def test_last_step_has_no_successor() -> None:
    assert next_step(ProvisioningStep.ACTIVATE) is None


@pytest.mark.parametrize(
    ("step", "status"),
    [
        (ProvisioningStep.VALIDATE, ProvisioningStatus.VALIDATED),
        (ProvisioningStep.GENERATE_CONFIG, ProvisioningStatus.CONFIG_GENERATED),
        (ProvisioningStep.PURCHASE_NUMBER, ProvisioningStatus.NUMBER_PURCHASED),
        (ProvisioningStep.CREATE_AGENT, ProvisioningStatus.AGENT_CREATED),
        (ProvisioningStep.LINK_NUMBER, ProvisioningStatus.NUMBER_LINKED),
        (ProvisioningStep.VERIFY, ProvisioningStatus.VERIFIED),
        (ProvisioningStep.ACTIVATE, ProvisioningStatus.ACTIVE),
    ],
)
def test_step_to_status(step: ProvisioningStep, status: ProvisioningStatus) -> None:
    assert status_after(step) is status


def test_terminal_and_in_flight_partition_every_status() -> None:
    """The partial unique index is built from this split; a gap would drop a run."""
    assert set(ProvisioningStatus) == TERMINAL_RUN_STATUSES | IN_FLIGHT_RUN_STATUSES
    assert set() == TERMINAL_RUN_STATUSES & IN_FLIGHT_RUN_STATUSES


def test_compensating_still_owns_the_tenant() -> None:
    """Releasing a number is in-flight work; a new run must not start under it."""
    assert ProvisioningStatus.COMPENSATING in IN_FLIGHT_RUN_STATUSES
    assert ProvisioningStatus.COMPENSATED in TERMINAL_RUN_STATUSES


def test_enum_values_are_stable_strings() -> None:
    """Values are persisted, so renaming one is a migration, not a refactor."""
    assert ProvisioningStatus.NUMBER_PURCHASED.value == "number_purchased"
    assert ProvisioningStep.PURCHASE_NUMBER.value == "purchase_number"
