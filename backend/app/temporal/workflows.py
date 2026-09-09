"""The provisioning workflow.

Replaces the polling engine's scheduling with a durable one. The step *sequence*
and every safety guarantee are unchanged; what Temporal takes over is retry,
backoff, timeout and crash recovery — the parts the POC implemented by hand with
``next_attempt_at`` and a lease.

    VALIDATE → GENERATE_CONFIG → BILLING_GATE
                                     │
                       ┌─────────────┴──────────────┐
                       │ not entitled: park + wait  │  (loop, never fails)
                       └─────────────┬──────────────┘
                                     ▼
          ┌──────── saga ────────────────────────────────┐
          │ PURCHASE_NUMBER → CREATE_AGENT →             │
          │ LINK_NUMBER → VERIFY                         │
          └──────────────────┬───────────────────────────┘
                             │ any failure
                             ▼
                   compensate → notify → fail
                             │ success
                             ▼
                          ACTIVATE

Two design points worth stating, because both are money-safety properties.

**The billing gate is a loop, not a failure.** A tenant who has not paid is not
a broken tenant. The workflow parks, waits for a signal or a timer, and asks
again — indefinitely, until the workflow's own timeout. Failing into
compensation would release a number for a customer one card entry away from
being entitled.

**The saga starts at the purchase.** Everything before it is free and
reversible; everything from ``PURCHASE_NUMBER`` onward has acquired something
that costs money every month until released. So compensation covers exactly that
span, and ``ACTIVATE`` sits outside it — a failure to send the welcome email
must not release a number that is working.

Workflow code is deterministic and replayed from history, so it does no I/O,
reads no clock except ``workflow.now()``, and holds no database handle. Every
side effect is an activity.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    # Imported outside the sandbox: these are plain constants and dataclasses,
    # but the enum module pulls in SQLAlchemy, which the sandbox rightly refuses
    # to re-import per workflow task.
    from app.models.enums import ProvisioningStep
    from app.temporal.shared import (
        BILLING_UPDATED_SIGNAL,
        CURRENT_STEP_QUERY,
        ERROR_BILLING_BLOCKED,
        CompensationOutcome,
        ProvisionInput,
        StepInput,
        StepOutcome,
    )


#: Steps before any money is spent. Free to retry, nothing to undo.
_PRE_PURCHASE: tuple[ProvisioningStep, ...] = (
    ProvisioningStep.VALIDATE,
    ProvisioningStep.GENERATE_CONFIG,
)

#: The saga span. From the first of these onward, a failure owes compensation.
_SAGA: tuple[ProvisioningStep, ...] = (
    ProvisioningStep.PURCHASE_NUMBER,
    ProvisioningStep.CREATE_AGENT,
    ProvisioningStep.LINK_NUMBER,
    ProvisioningStep.VERIFY,
)


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    """What the workflow reports when it finishes."""

    run_id: str
    status: str
    compensated: bool = False


def _retry_policy(maximum_attempts: int) -> RetryPolicy:
    """Backoff for a vendor call.

    The ladder mirrors the POC's ``PROVISIONING_BACKOFF_S`` (5s, doubling, capped
    at ten minutes) so behaviour under a flaky vendor is recognisably the same.

    ``non_retryable_error_types`` is the important part: a billing block or any
    terminal application error stops immediately. Without it Temporal would
    cheerfully retry a rejected signup until the attempt budget ran out, turning
    a clear failure into a slow one — and would repeatedly re-run a billing gate
    that cannot pass until a human acts.
    """
    return RetryPolicy(
        initial_interval=timedelta(seconds=5),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=10),
        maximum_attempts=maximum_attempts,
        non_retryable_error_types=[
            ERROR_BILLING_BLOCKED,
            "invalid_input",
            "not_found",
            "configuration_error",
            "forbidden",
            "unauthenticated",
            "dry_run_blocked",
        ],
    )


@dataclass(frozen=True, slots=True)
class ProvisionOptions:
    """Tunables passed in at start, so the workflow reads no configuration.

    Workflow code must be deterministic across replays, and reading settings at
    runtime would let a redeploy change the behaviour of an execution already in
    flight. Passing them as input pins them to the execution.
    """

    max_attempts: int = 5
    activity_timeout_s: int = 120
    billing_recheck_interval_s: int = 300


@workflow.defn(name="ProvisionTenantWorkflow")
class ProvisionTenantWorkflow:
    """Takes one tenant from signup to ACTIVE, durably."""

    def __init__(self) -> None:
        self._current_step: str = "pending"
        self._billing_changed: bool = False

    # ---- signals and queries --------------------------------------------
    @workflow.signal(name=BILLING_UPDATED_SIGNAL)
    async def billing_updated(self) -> None:
        """Billing state changed — stop waiting and re-check.

        Sent by the Stripe webhook. It only wakes the wait; it never grants
        anything. The gate is re-evaluated from the database when the workflow
        resumes, so a forged or mistaken signal cannot authorize a purchase.
        """
        self._billing_changed = True

    @workflow.query(name=CURRENT_STEP_QUERY)
    def current_step(self) -> str:
        """The workflow's own view of progress, for debugging.

        The database remains the authoritative answer; this exists so the
        Temporal UI shows something meaningful without a join.
        """
        return self._current_step

    # ---- the run ---------------------------------------------------------
    @workflow.run
    async def run(
        self, payload: ProvisionInput, options: ProvisionOptions | None = None
    ) -> ProvisionResult:
        opts = options or ProvisionOptions()

        for step in _PRE_PURCHASE:
            await self._step(payload, step, opts)

        await self._billing_gate(payload, opts)

        # ---- saga --------------------------------------------------------
        try:
            for step in _SAGA:
                await self._step(payload, step, opts)
        except ActivityError as exc:
            # Something was already bought. Release it before failing, and do
            # that before the notification: stopping the money is the urgent
            # part, telling the customer is not.
            self._current_step = "compensating"
            await workflow.execute_activity(
                "compensate_provisioning_run",
                payload,
                start_to_close_timeout=timedelta(seconds=opts.activity_timeout_s),
                # Compensation is idempotent by construction, so it is retried
                # harder than a forward step: leaving a number unreleased costs
                # money every month, forever.
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(minutes=10),
                    maximum_attempts=10,
                ),
            )
            await self._notify_failure(payload, opts)
            raise ApplicationError(
                "provisioning failed and was compensated",
                type="provisioning_compensated",
                non_retryable=True,
            ) from exc

        # Outside the saga on purpose: activation sends the welcome email and
        # flips the tenant live. A failure here must not release a number that
        # is already working.
        await self._step(payload, ProvisioningStep.ACTIVATE, opts)

        self._current_step = "active"
        return ProvisionResult(run_id=payload.run_id, status="active")

    # ---- pieces ----------------------------------------------------------
    async def _step(
        self, payload: ProvisionInput, step: ProvisioningStep, opts: ProvisionOptions
    ) -> StepOutcome:
        """Execute one step, marking the run failed if its retries run out."""
        self._current_step = step.value
        try:
            # `result_type` so the payload is deserialized into the dataclass
            # rather than left as a dict — without it the return is untyped and
            # a caller reading `.status` would fail at runtime, not here.
            # `execute_activity` is typed as returning Any because the
            # activity is addressed by name; the annotation is what pins it.
            outcome: StepOutcome = await workflow.execute_activity(
                "execute_provisioning_step",
                StepInput(run_id=payload.run_id, step=step.value),
                result_type=StepOutcome,
                start_to_close_timeout=timedelta(seconds=opts.activity_timeout_s),
                retry_policy=_retry_policy(opts.max_attempts),
            )
            return outcome
        except ActivityError:
            # The retry budget is spent, or the error was non-retryable. Only
            # the workflow knows that, which is why the activity left the step
            # PENDING rather than declaring it dead.
            if step not in _SAGA:
                await self._fail(payload, step, opts)
            raise

    async def _billing_gate(self, payload: ProvisionInput, opts: ProvisionOptions) -> None:
        """Hold here until the tenant is entitled.

        A loop rather than a failure. Each pass runs the real gate step — so the
        decision, the audit trail and the step row are identical to the polling
        path — and on refusal parks the run and waits for either the Stripe
        signal or the re-check timer.

        The timer matters as much as the signal: a webhook that is never
        delivered must not strand a paying customer, so the workflow asks again
        on its own. The signal is the fast path, not the only path.
        """
        while True:
            self._current_step = ProvisioningStep.BILLING_GATE.value
            try:
                await workflow.execute_activity(
                    "execute_provisioning_step",
                    StepInput(run_id=payload.run_id, step=ProvisioningStep.BILLING_GATE.value),
                    start_to_close_timeout=timedelta(seconds=opts.activity_timeout_s),
                    retry_policy=_retry_policy(opts.max_attempts),
                )
                return
            except ActivityError as exc:
                if not _is_billing_block(exc):
                    await self._fail(payload, ProvisioningStep.BILLING_GATE, opts)
                    raise

            await workflow.execute_activity(
                "mark_provisioning_blocked",
                StepInput(run_id=payload.run_id, step=ProvisioningStep.BILLING_GATE.value),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            self._current_step = "billing_blocked"
            self._billing_changed = False
            # Two ways out, and the timeout is the *normal* one.
            #
            # `wait_condition` raises rather than returning when its timer
            # expires, so the TimeoutError is caught and treated as "time to ask
            # again". Letting it propagate would fail the workflow on the first
            # re-check — turning a customer who simply has not paid yet into a
            # failed provisioning run, which is exactly what parking exists to
            # avoid.
            # Suppressed, because the timeout is the re-check path: the Stripe
            # signal never came, so loop and re-evaluate the gate from the
            # database. A webhook that is never delivered must not strand a
            # paying customer.
            with contextlib.suppress(TimeoutError):
                await workflow.wait_condition(
                    lambda: self._billing_changed,
                    timeout=timedelta(seconds=opts.billing_recheck_interval_s),
                )

    async def _fail(
        self,
        payload: ProvisionInput,
        step: ProvisioningStep | None,
        opts: ProvisionOptions,
    ) -> None:
        await workflow.execute_activity(
            "fail_provisioning_run",
            StepInput(run_id=payload.run_id, step=step.value if step else ""),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        await self._notify_failure(payload, opts)

    async def _notify_failure(self, payload: ProvisionInput, opts: ProvisionOptions) -> None:
        await workflow.execute_activity(
            "notify_provisioning_failure",
            payload,
            start_to_close_timeout=timedelta(seconds=opts.activity_timeout_s),
            # Few attempts: the notification is best-effort, and holding a
            # failed run open for a down email provider helps nobody.
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


def _is_billing_block(exc: ActivityError) -> bool:
    """Whether this activity failure was the money gate refusing.

    Inspects ``ApplicationError.type``: an exception crossing the activity
    boundary loses its class and arrives as a type string, so the workflow
    cannot branch on ``isinstance``.
    """
    cause = exc.cause
    return isinstance(cause, ApplicationError) and cause.type == ERROR_BILLING_BLOCKED


__all__ = [
    "CompensationOutcome",
    "ProvisionOptions",
    "ProvisionResult",
    "ProvisionTenantWorkflow",
]
