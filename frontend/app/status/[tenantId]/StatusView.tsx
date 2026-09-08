"use client";

/**
 * The provisioning status page.
 *
 * Polls until the run reaches a terminal state, then stops. All seven steps are
 * shown from the first render — the backend writes them at signup — so the page
 * answers "how far along is this, and what failed" rather than growing a list as
 * things happen.
 */

import { useEffect, useState } from "react";

import { ApiError, getPhoneOrNull, getProvisioning, getTenant, listCalls } from "@/lib/api";
import {
  STEP_LABELS,
  isTerminal,
  type CallView,
  type PhoneNumberView,
  type ProvisioningView,
  type StepStatus,
  type TenantView,
} from "@/lib/types";

const POLL_INTERVAL_MS = 2000;

interface Snapshot {
  tenant: TenantView;
  provisioning: ProvisioningView;
  phone: PhoneNumberView | null;
  calls: CallView[];
}

export function StatusView({ tenantId }: { tenantId: string }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const status = snapshot?.provisioning.status;
  const settled = status !== undefined && isTerminal(status);

  useEffect(() => {
    // One effect owns the whole polling lifecycle: fetch now, then on an
    // interval, and stop entirely once the run reaches a terminal state. A page
    // left open overnight should not keep hitting the API.
    //
    // `settled` in the dependencies means the effect re-runs once as the run
    // finishes, costing one extra fetch. That is the price of keeping the whole
    // lifecycle in one place, and it happens once per page rather than per poll.
    let cancelled = false;

    async function refresh() {
      try {
        const [tenant, provisioning, phone, calls] = await Promise.all([
          getTenant(tenantId),
          getProvisioning(tenantId),
          getPhoneOrNull(tenantId),
          listCalls(tenantId),
        ]);
        if (cancelled) return;
        setSnapshot({ tenant, provisioning, phone, calls });
        setError(null);
      } catch (caught) {
        if (cancelled) return;
        setError(
          caught instanceof ApiError
            ? caught
            : new ApiError("Could not load status.", { status: 0 }),
        );
      }
    }

    void refresh();
    if (settled)
      return () => {
        cancelled = true;
      };

    const timer = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [tenantId, settled]);

  if (error && !snapshot) {
    return (
      <div
        role="alert"
        className="rounded-xl border border-danger-line bg-danger-soft px-4 py-3.5"
      >
        <p className="text-sm font-medium text-danger-ink">{error.message}</p>
        {error.correlationId && (
          <p className="mt-1.5 font-mono text-xs text-danger-ink/70">
            Reference: {error.correlationId}
          </p>
        )}
      </div>
    );
  }

  if (!snapshot) {
    return (
      <div className="space-y-4" aria-busy>
        <div className="h-8 w-52 animate-pulse rounded-lg bg-surface-2" />
        <div className="card h-56 animate-pulse" />
        <p className="text-sm text-muted">Loading…</p>
      </div>
    );
  }

  const { tenant, provisioning, phone, calls } = snapshot;
  const active = provisioning.status === "active";
  const failed = provisioning.status === "failed" || provisioning.status === "compensated";
  const percent = Math.round(
    (provisioning.completed_steps / Math.max(provisioning.total_steps, 1)) * 100,
  );

  return (
    <div className="space-y-8">
      <section className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">{tenant.name}</h1>
          <p className="mt-1.5 text-sm text-muted">
            {tenant.business_type.replace("_", " ")} · {tenant.timezone}
          </p>
        </div>
        <RunBadge active={active} failed={failed} />
      </section>

      {active && phone && (
        <section className="rounded-xl border border-success-line bg-success-soft p-6">
          <p className="text-sm font-medium text-success-ink">
            Your receptionist is live
          </p>
          <p className="mt-2 font-mono text-3xl font-semibold tracking-tight text-success-ink">
            {phone.e164}
          </p>
          <p className="mt-2.5 text-sm text-success-ink/80">
            Call it to hear the greeting. Every call appears below with a summary.
          </p>
        </section>
      )}

      {failed && (
        <section
          role="alert"
          className="rounded-xl border border-danger-line bg-danger-soft p-6"
        >
          <p className="text-sm font-medium text-danger-ink">Setup did not complete</p>
          {provisioning.last_error && (
            <p className="mt-2.5 rounded-lg bg-danger-line/25 p-3 font-mono text-xs break-words text-danger-ink">
              {provisioning.last_error}
            </p>
          )}
          <p className="mt-2.5 text-sm text-danger-ink/80">
            Nothing is being charged. Reference {provisioning.correlation_id}.
          </p>
        </section>
      )}

      <section className="card overflow-hidden">
        <div className="flex items-baseline justify-between gap-3 px-5 pt-5">
          <h2 className="text-sm font-semibold">Setup progress</h2>
          <span className="text-xs text-muted">
            {provisioning.completed_steps} of {provisioning.total_steps}
            {!settled && " · updating"}
          </span>
        </div>

        <div
          className="mx-5 mt-3 h-1.5 overflow-hidden rounded-full bg-surface-2"
          role="progressbar"
          aria-valuenow={provisioning.completed_steps}
          aria-valuemin={0}
          aria-valuemax={provisioning.total_steps}
          aria-label="Setup progress"
        >
          <div
            className={`h-full rounded-full transition-[width] duration-500 ${
              failed ? "bg-danger" : active ? "bg-success" : "bg-accent"
            }`}
            style={{ width: `${percent}%` }}
          />
        </div>

        <ol className="mt-4 divide-y divide-line border-t border-line">
          {provisioning.steps.map((step) => (
            <li key={step.step_name} className="flex gap-3 px-5 py-3.5">
              <StatusDot status={step.status} />
              <div className="min-w-0 flex-1">
                <p
                  className={`text-sm ${
                    step.status === "pending" ? "text-muted" : ""
                  }`}
                >
                  {STEP_LABELS[step.step_name] ?? step.step_name}
                </p>
                {step.attempt > 1 && (
                  <p className="mt-0.5 text-xs text-muted">attempt {step.attempt}</p>
                )}
                {step.error && (
                  <p className="mt-1.5 rounded-lg bg-danger-soft p-2 font-mono text-xs break-words text-danger">
                    {step.error}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section>
        <h2 className="text-sm font-semibold">Recent calls</h2>
        {calls.length === 0 ? (
          <p className="mt-2.5 rounded-xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">
            {active
              ? "No calls yet. Ring your number and it will show up here."
              : "Calls will appear once setup finishes."}
          </p>
        ) : (
          <ul className="mt-3 space-y-3">
            {calls.map((call) => (
              <li key={call.id} className="card p-5">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="text-sm font-medium">
                    {call.caller_name ?? "Unknown caller"}
                    {call.from_e164 && (
                      <span className="ml-2 font-mono text-xs font-normal text-muted">
                        {call.from_e164}
                      </span>
                    )}
                  </p>
                  {call.urgency !== null && call.urgency >= 4 && (
                    <span className="shrink-0 rounded-full border border-warn-line bg-warn-soft px-2.5 py-0.5 text-xs font-medium text-warn">
                      urgent
                    </span>
                  )}
                </div>
                <p className="mt-2 text-sm leading-relaxed">
                  {call.summary ?? "Summary not available for this call."}
                </p>
                {call.callback_number && (
                  <p className="mt-2.5 text-xs text-muted">
                    Callback: <span className="font-mono">{call.callback_number}</span>{" "}
                    (as stated by the caller)
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function RunBadge({ active, failed }: { active: boolean; failed: boolean }) {
  if (active) {
    return (
      <span className="rounded-full border border-success-line bg-success-soft px-3 py-1 text-xs font-medium text-success-ink">
        Active
      </span>
    );
  }
  if (failed) {
    return (
      <span className="rounded-full border border-danger-line bg-danger-soft px-3 py-1 text-xs font-medium text-danger-ink">
        Stopped
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs font-medium text-accent">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" aria-hidden />
      Setting up
    </span>
  );
}

const DOT_CLASSES: Record<StepStatus, string> = {
  succeeded: "bg-success",
  running: "bg-accent animate-pulse ring-4 ring-accent-soft",
  failed: "bg-danger",
  pending: "bg-line-strong",
  skipped: "bg-line-strong",
};

function StatusDot({ status }: { status: StepStatus }) {
  return (
    <span
      aria-label={status}
      role="img"
      className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${DOT_CLASSES[status]}`}
    />
  );
}
