import { Alert } from "@/components/ui/Alert";
import { ProgressBar } from "@/components/ui/Feedback";
import { formatCalendarDate, formatNumber } from "@/lib/format";
import type { UsageView } from "@/lib/types";

/** This period's minutes against the plan. Renders the page's one progress bar. */
export function UsageMeter({ usage }: { usage: UsageView }) {
  const tone = usage.over_limit ? "danger" : usage.warning ? "warn" : "accent";
  const over = usage.call_minutes - usage.included_minutes;
  return (
    <div>
      <div className="flex items-end justify-between gap-4">
        <p className="min-w-0">
          <span className="text-3xl font-bold tracking-tight tabular-nums">
            {formatNumber(usage.call_minutes)}
          </span>
          <span className="text-sm text-muted"> / {formatNumber(usage.included_minutes)} minutes</span>
        </p>
        <p className="text-sm font-semibold tabular-nums text-ink-2">{usage.percent_used}%</p>
      </div>
      <ProgressBar
        className="mt-3"
        value={usage.percent_used}
        label="Minutes used this period"
        tone={tone}
        size="lg"
      />
      <p className="mt-2.5 text-sm text-muted">
        {over > 0
          ? `${formatNumber(over)} minutes over your included minutes`
          : `${formatNumber(Math.max(0, -over))} minutes remaining`}{" "}
        · period ends {formatCalendarDate(usage.period_end)}
      </p>
    </div>
  );
}

/** Only when there is something to say. An overage is an alert; nearing it is not. */
export function UsageNotice({ usage }: { usage: UsageView }) {
  if (usage.over_limit) {
    return (
      <Alert tone="danger" title="You've used your included minutes">
        {usage.blocks_on_overage
          ? "You have used your included minutes. Add a payment method to keep taking calls."
          : "You are over your included minutes. Additional minutes are billed at your plan rate."}
      </Alert>
    );
  }
  if (usage.warning) {
    return (
      <Alert tone="warn" title="You're close to your included minutes">
        You&apos;ve used {usage.percent_used}% of this period&apos;s minutes.
        {usage.blocks_on_overage
          ? " Calls stop being answered when the trial's minutes run out."
          : " Calls keep being answered past the limit; extra minutes are billed at your plan rate."}
      </Alert>
    );
  }
  return null;
}
