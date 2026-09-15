import type { ReactNode } from "react";

import { Alert } from "@/components/ui/Alert";
import { cx } from "@/components/ui/cx";
import { Skeleton } from "@/components/ui/Feedback";
import {
  ActivityIcon,
  AlertTriangleIcon,
  BuildingIcon,
  CheckCircleIcon,
  CreditCardIcon,
} from "@/components/ui/icons";
import { formatNumber } from "@/lib/format";
import type { Tone } from "@/lib/tone";
import type { RunSummaryView } from "@/lib/types";

import { overviewCounts, type OverviewCounts } from "./support";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-ink-2",
  accent: "bg-accent-soft text-accent",
  success: "bg-success-soft text-success",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
};

const STATS: ReadonlyArray<{
  key: keyof OverviewCounts;
  label: string;
  hint: string;
  tone: Tone;
  icon: ReactNode;
}> = [
  { key: "tenants", label: "Tenants", hint: "With a provisioning run", tone: "neutral", icon: <BuildingIcon size={16} /> },
  { key: "active", label: "Active receptionists", hint: "Live and answering", tone: "success", icon: <CheckCircleIcon size={16} /> },
  { key: "inProgress", label: "In progress", hint: "Provisioning now", tone: "accent", icon: <ActivityIcon size={16} /> },
  { key: "billing", label: "Waiting on billing", hint: "Parked until entitled", tone: "warn", icon: <CreditCardIcon size={16} /> },
  { key: "failed", label: "Provisioning failures", hint: "Failed or abandoned", tone: "danger", icon: <AlertTriangleIcon size={16} /> },
];

/**
 * Platform totals. Numbers only — business names belong to the runs table, and
 * repeating them here would say the same thing twice.
 */
export function OverviewStats({
  runs,
  error,
}: {
  runs: readonly RunSummaryView[] | null;
  error: string | null;
}) {
  const counts = runs ? overviewCounts(runs) : null;

  return (
    <section aria-labelledby="overview-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id="overview-heading" className="text-sm font-semibold">
          Overview
        </h2>
        <p className="text-xs text-subtle">Based on the 200 most recent provisioning runs.</p>
      </div>

      {error ? (
        <Alert tone="warn" title="Overview unavailable" className="mt-3">
          {error}
        </Alert>
      ) : (
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {STATS.map((stat) =>
            counts ? (
              <div key={stat.key} role="group" aria-label={stat.label} className="card p-4 sm:p-5">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-[0.8125rem] font-medium leading-snug text-muted">{stat.label}</p>
                  <span
                    className={cx(
                      "flex size-8 shrink-0 items-center justify-center rounded-lg",
                      TONE_CLASSES[stat.tone],
                    )}
                  >
                    {stat.icon}
                  </span>
                </div>
                <p className="mt-3 text-[1.75rem] font-bold leading-none tracking-tight tabular-nums">
                  {formatNumber(counts[stat.key])}
                </p>
                <p className="mt-1.5 text-xs text-subtle">{stat.hint}</p>
              </div>
            ) : (
              <div key={stat.key} className="card space-y-3 p-4 sm:p-5">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-7 w-12" />
              </div>
            ),
          )}
        </div>
      )}
    </section>
  );
}
