import { cx } from "@/components/ui/cx";
import { CheckIcon } from "@/components/ui/icons";

/**
 * The five stages of getting set up. The first four are the signup form; the
 * fifth, Activate, is the provisioning status page — so the indicator carries
 * straight through from the form into setup.
 */
export const ONBOARDING_STEPS = ["Business", "Receptionist", "Phone", "Review", "Activate"] as const;

export function OnboardingStepper({
  current,
  complete = false,
  onStepClick,
}: {
  current: number;
  complete?: boolean;
  /** Offered for steps already passed, so a user can jump back to edit. */
  onStepClick?: (index: number) => void;
}) {
  const total = ONBOARDING_STEPS.length;
  const shown = Math.min(current, total - 1);

  return (
    <nav aria-label="Setup steps">
      <div className="md:hidden">
        <p className="text-sm font-semibold text-muted">
          Step {shown + 1} of {total} · <span className="text-ink">{ONBOARDING_STEPS[shown]}</span>
        </p>
        <div className="progress progress-sm mt-2.5" aria-hidden>
          <div
            className="progress-fill progress-accent"
            style={{ width: `${((complete ? total : shown + 1) / total) * 100}%` }}
          />
        </div>
      </div>

      <ol className="hidden items-center gap-3 md:flex">
        {ONBOARDING_STEPS.map((label, index) => {
          const done = complete || index < current;
          const isCurrent = !complete && index === current;
          const clickable = Boolean(onStepClick) && index < current && !complete;
          const content = (
            <>
              <span
                className={cx(
                  "flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-bold transition-colors",
                  done
                    ? "bg-accent text-accent-fg"
                    : isCurrent
                      ? "border-2 border-accent text-accent"
                      : "border border-line-strong text-subtle",
                )}
              >
                {done ? <CheckIcon size={14} strokeWidth={2.8} /> : index + 1}
              </span>
              <span
                className={cx(
                  "text-sm font-semibold whitespace-nowrap",
                  done || isCurrent ? "text-ink" : "text-muted",
                )}
              >
                {label}
                {done && <span className="sr-only"> (completed)</span>}
              </span>
            </>
          );
          return (
            <li
              key={label}
              aria-current={isCurrent ? "step" : undefined}
              className="flex min-w-0 flex-1 items-center gap-3 last:flex-none"
            >
              {clickable ? (
                <button
                  type="button"
                  onClick={() => onStepClick?.(index)}
                  className="flex items-center gap-2 rounded-full transition-opacity hover:opacity-75"
                >
                  {content}
                </button>
              ) : (
                <span className="flex items-center gap-2">{content}</span>
              )}
              {index < total - 1 && (
                <span
                  aria-hidden
                  className={cx("h-0.5 min-w-4 flex-1 rounded-full", done ? "bg-accent" : "bg-line-strong")}
                />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
