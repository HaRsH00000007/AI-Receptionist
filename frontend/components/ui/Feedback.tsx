import type { ReactNode } from "react";

import type { Tone } from "@/lib/tone";

import { cx } from "./cx";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton", className)} aria-hidden />;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "flex flex-col items-center justify-center rounded-xl border border-dashed border-line-strong px-6 py-12 text-center",
        className,
      )}
    >
      {icon && (
        <div className="mb-4 flex size-11 items-center justify-center rounded-xl bg-surface-2 text-muted">
          {icon}
        </div>
      )}
      <p className="text-[0.9375rem] font-semibold">{title}</p>
      {description && <p className="mt-1.5 max-w-sm text-sm text-muted">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/** A determinate meter. `value` is clamped, so an overage never draws past the end. */
export function ProgressBar({
  value,
  max = 100,
  label,
  tone = "accent",
  size = "md",
  className,
}: {
  value: number;
  max?: number;
  label: string;
  tone?: Exclude<Tone, "neutral">;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const clamped = Math.max(0, Math.min(value, max));
  const percent = max > 0 ? (clamped / max) * 100 : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={clamped}
      className={cx("progress", `progress-${size}`, className)}
    >
      <div className={cx("progress-fill", `progress-${tone}`)} style={{ width: `${percent}%` }} />
    </div>
  );
}
