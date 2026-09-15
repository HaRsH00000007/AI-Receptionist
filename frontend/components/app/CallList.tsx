"use client";

/**
 * A list of calls, one row per call.
 *
 * One DOM for every width: a grid row on desktop that stacks on mobile. Two
 * separate renderings (a table and a card list) would put every caller in the
 * document twice, and a screen reader would read both.
 *
 * `compact` is for narrow cards such as the overview's: the intent moves into
 * the summary line instead of taking a column of its own.
 */

import { Badge } from "@/components/ui/Badge";
import { cx } from "@/components/ui/cx";
import { ChevronRightIcon } from "@/components/ui/icons";
import { formatDuration, formatPhone, formatRelative, humanize, initials } from "@/lib/format";
import { callStatusMeta, isUrgent } from "@/lib/status";
import type { CallView } from "@/lib/types";

export function callerName(call: CallView): string {
  if (call.caller_name) return call.caller_name;
  if (call.from_e164) return formatPhone(call.from_e164);
  return "Unknown caller";
}

// Written out in full so Tailwind can see every class.
const GRID = {
  full: "grid grid-cols-[2.25rem_minmax(0,1fr)_auto] gap-x-3 md:grid-cols-[2.25rem_minmax(0,2.6fr)_7.5rem_4.5rem_minmax(0,1.1fr)_7.5rem_1rem] md:gap-x-4",
  compact:
    "grid grid-cols-[2.25rem_minmax(0,1fr)_auto] gap-x-3 md:grid-cols-[2.25rem_minmax(0,1fr)_7rem_4.5rem_7.5rem_1rem] md:gap-x-4",
} as const;

export function CallList({
  calls,
  onSelect,
  now,
  compact = false,
}: {
  calls: CallView[];
  onSelect: (call: CallView) => void;
  /** The reference time for "5 minutes ago", passed in so rendering stays pure. */
  now: number;
  compact?: boolean;
}) {
  const grid = compact ? GRID.compact : GRID.full;

  return (
    <div>
      <div
        aria-hidden
        className={cx(grid, "hidden border-b border-line bg-surface-2 px-5 py-2.5 text-xs font-semibold text-muted md:grid")}
      >
        <span />
        <span>Caller</span>
        <span>Time</span>
        <span>Duration</span>
        {!compact && <span>Intent</span>}
        <span className="text-right">Status</span>
        <span />
      </div>
      <ul className="divide-y divide-line">
        {calls.map((call) => {
          const status = callStatusMeta(call.status);
          const urgent = isUrgent(call.urgency);
          const name = callerName(call);
          const summary = call.summary ?? "Summary not available yet.";
          return (
            <li key={call.id}>
              <button
                type="button"
                onClick={() => onSelect(call)}
                aria-label={`View call from ${name}`}
                className={cx(
                  grid,
                  "w-full items-start px-4 py-3.5 text-left transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none md:items-center md:px-5",
                )}
              >
                <span
                  aria-hidden
                  className="flex size-9 items-center justify-center rounded-full bg-surface-3 text-xs font-bold text-ink-2"
                >
                  {initials(call.caller_name ?? "")}
                </span>

                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-ink">{name}</span>
                    {urgent && <Badge tone="warn">Urgent</Badge>}
                  </span>
                  <span className="mt-0.5 line-clamp-2 text-sm text-muted md:line-clamp-1">
                    {compact && call.intent && (
                      <span className="hidden font-medium text-ink-2 md:inline">{humanize(call.intent)} · </span>
                    )}
                    {summary}
                  </span>
                  <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted md:hidden">
                    <span>{formatDuration(call.duration_s)}</span>
                    {call.intent && (
                      <>
                        <span aria-hidden>·</span>
                        <span>{humanize(call.intent)}</span>
                      </>
                    )}
                    <span aria-hidden>·</span>
                    <span>{status.label}</span>
                  </span>
                </span>

                <span className="truncate text-right text-xs whitespace-nowrap text-muted md:text-left md:text-sm">
                  {call.started_at ? formatRelative(call.started_at, now) : "—"}
                </span>
                <span className="hidden text-sm tabular-nums text-ink-2 md:block">
                  {formatDuration(call.duration_s)}
                </span>
                {!compact && (
                  <span className="hidden truncate text-sm text-ink-2 md:block">{humanize(call.intent)}</span>
                )}
                <span className="hidden justify-end md:flex">
                  <Badge tone={status.tone}>{status.label}</Badge>
                </span>
                <ChevronRightIcon size={16} className="hidden text-subtle md:block" />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
