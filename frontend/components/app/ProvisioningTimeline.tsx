/**
 * The provisioning run, step by step.
 *
 * Every step is shown from the first render — the backend writes them all at
 * signup — so the timeline answers "how far along is this, and what failed"
 * rather than growing as things happen.
 */

import { Badge } from "@/components/ui/Badge";
import { cx } from "@/components/ui/cx";
import { CheckIcon, XIcon } from "@/components/ui/icons";
import { formatDuration } from "@/lib/format";
import { STEP_COPY, friendlyError, stepLabel } from "@/lib/status";
import { isTerminal, type ProvisioningView, type StepStatus, type StepView } from "@/lib/types";

type Display = StepStatus | "active";

function durationOf(step: StepView): string | null {
  if (!step.started_at || !step.finished_at) return null;
  const seconds = (new Date(step.finished_at).getTime() - new Date(step.started_at).getTime()) / 1000;
  return Number.isFinite(seconds) && seconds >= 0 ? formatDuration(Math.max(1, seconds)) : null;
}

const SCREEN_READER_STATUS: Record<Display, string> = {
  succeeded: "Done",
  running: "In progress",
  active: "Up next",
  failed: "Failed",
  pending: "Not started",
  skipped: "Skipped",
};

export function ProvisioningTimeline({ provisioning }: { provisioning: ProvisioningView }) {
  const moving = !isTerminal(provisioning.status) && provisioning.status !== "billing_blocked";
  const runningIndex = provisioning.steps.findIndex((step) => step.status === "running");
  // With nothing marked running, the first pending step is what happens next.
  const activeIndex =
    runningIndex >= 0
      ? runningIndex
      : moving
        ? provisioning.steps.findIndex((step) => step.status === "pending")
        : -1;

  return (
    <ol className="relative">
      {provisioning.steps.map((step, index) => {
        const display: Display =
          index === activeIndex && step.status === "pending" ? "active" : step.status;
        const inProgress = display === "running" || display === "active";
        const last = index === provisioning.steps.length - 1;
        const duration = durationOf(step);
        return (
          <li key={step.step_name} className={cx("relative flex gap-4", !last && "pb-6")}>
            {!last && (
              <span
                aria-hidden
                className={cx(
                  "absolute top-9 bottom-1 left-[15px] w-0.5 rounded-full",
                  step.status === "succeeded" ? "bg-success" : "bg-line",
                )}
              />
            )}
            <StepMarker display={display} index={index} />
            <div className="min-w-0 flex-1 pt-1">
              <span className="sr-only">{SCREEN_READER_STATUS[display]}: </span>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <p
                  className={cx(
                    "text-sm",
                    display === "pending" || display === "skipped"
                      ? "font-medium text-muted"
                      : "font-semibold text-ink",
                  )}
                >
                  {stepLabel(step.step_name)}
                </p>
                {inProgress && (
                  <Badge tone="accent" dot pulse>
                    {display === "running" ? "In progress" : "Up next"}
                  </Badge>
                )}
                {step.status === "failed" && <Badge tone="danger">Failed</Badge>}
                {step.status === "skipped" && <Badge>Skipped</Badge>}
              </div>
              {(inProgress || step.status === "failed") && STEP_COPY[step.step_name] && (
                <p className="mt-1 text-sm text-muted">{STEP_COPY[step.step_name]?.description}</p>
              )}
              {step.status === "succeeded" && duration && (
                <p className="mt-0.5 text-xs text-subtle">Done in {duration}</p>
              )}
              {step.attempt > 1 && <p className="mt-0.5 text-xs text-muted">Attempt {step.attempt}</p>}
              {step.error && <StepError raw={step.error} />}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function StepMarker({ display, index }: { display: Display; index: number }) {
  const base =
    "relative z-10 flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-bold";
  if (display === "succeeded") {
    return (
      <span aria-hidden className={cx(base, "animate-fade-in bg-success text-white")}>
        <CheckIcon size={15} strokeWidth={2.6} />
      </span>
    );
  }
  if (display === "failed") {
    return (
      <span aria-hidden className={cx(base, "bg-danger text-white")}>
        <XIcon size={15} strokeWidth={2.6} />
      </span>
    );
  }
  if (display === "running" || display === "active") {
    return (
      <span aria-hidden className={cx(base, "border-2 border-accent bg-accent-soft text-accent")}>
        <span className="absolute inset-0 rounded-full border-2 border-accent opacity-60 [animation:ring-pulse_1.8s_ease-out_infinite]" />
        <span className="size-2.5 rounded-full bg-accent" />
      </span>
    );
  }
  return (
    <span aria-hidden className={cx(base, "border border-line-strong bg-surface text-subtle")}>
      {index + 1}
    </span>
  );
}

/** A step's error, in words first and verbatim second. */
export function StepError({ raw }: { raw: string }) {
  const friendly = friendlyError(raw);
  return (
    <div className="mt-2.5 rounded-xl border border-danger-line bg-danger-soft px-3.5 py-3 text-sm text-danger-ink">
      <p className="font-semibold">{friendly.title}</p>
      <p className="mt-0.5 opacity-90">{friendly.explanation}</p>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer font-medium opacity-80 hover:opacity-100">
          Technical details
        </summary>
        <p className="mt-1.5 font-mono break-words opacity-90">{friendly.technical}</p>
      </details>
    </div>
  );
}
