import type { ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { cx } from "@/components/ui/cx";
import { RefreshIcon } from "@/components/ui/icons";
import { formatDateTime, formatRelative } from "@/lib/format";
import { stepLabel } from "@/lib/status";
import type { RunSummaryView } from "@/lib/types";

import { RunStatusBadge } from "./RunStatusBadge";
import { shortId } from "./support";

/*
 * One DOM for every width: a table from `md` up, and the same rows restacked
 * as cards below it. Rendering two separate layouts would put every button on
 * the page twice, which is twice the tab stops for a keyboard user.
 */
const CELL =
  "max-md:flex max-md:min-w-0 max-md:items-center max-md:justify-between max-md:gap-4 max-md:border-0 max-md:px-0 max-md:py-1.5";

function MobileLabel({ children }: { children: ReactNode }) {
  return <span className="shrink-0 text-xs font-medium text-subtle md:hidden">{children}</span>;
}

export function RunsTable({
  runs,
  busyRunId,
  onRetry,
  onAbandon,
  onDetails,
}: {
  runs: readonly RunSummaryView[];
  busyRunId: string | null;
  onRetry: (run: RunSummaryView) => void;
  onAbandon: (run: RunSummaryView) => void;
  onDetails: (run: RunSummaryView) => void;
}) {
  return (
    <div className="md:overflow-x-auto">
      <table className="data-table max-md:block">
        <caption className="sr-only">Provisioning runs and their status</caption>
        <thead className="max-md:hidden">
          <tr>
            <th scope="col">Business</th>
            <th scope="col">Status</th>
            <th scope="col">Step</th>
            <th scope="col">Attempt</th>
            <th scope="col">Started</th>
            <th scope="col">Last error</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody className="max-md:block">
          {runs.map((run) => {
            const busy = busyRunId === run.run_id;
            return (
              <tr
                key={run.run_id}
                className="max-md:block max-md:border-b max-md:border-line max-md:px-4 max-md:py-4 max-md:last:border-b-0"
              >
                <td className="max-md:block max-md:border-0 max-md:px-0 max-md:pt-0 max-md:pb-2">
                  <p className="font-semibold text-ink">{run.business_name}</p>
                  <p className="mt-0.5 font-mono text-xs text-subtle" title={run.tenant_id}>
                    {shortId(run.tenant_id)}
                  </p>
                </td>
                <td className={CELL}>
                  <MobileLabel>Status</MobileLabel>
                  <RunStatusBadge status={run.status} />
                </td>
                <td className={cx(CELL, "text-ink-2")}>
                  <MobileLabel>Step</MobileLabel>
                  <span className="max-md:text-right">
                    {run.current_step ? stepLabel(run.current_step) : "—"}
                  </span>
                </td>
                <td className={cx(CELL, "tabular-nums")}>
                  <MobileLabel>Attempt</MobileLabel>
                  {run.attempt}
                </td>
                <td className={cx(CELL, "whitespace-nowrap text-muted")}>
                  <MobileLabel>Started</MobileLabel>
                  {run.started_at ? (
                    <time dateTime={run.started_at} title={formatDateTime(run.started_at)}>
                      {formatRelative(run.started_at)}
                    </time>
                  ) : (
                    "Not started"
                  )}
                </td>
                <td className={cx(CELL, "md:max-w-[16rem]")}>
                  <MobileLabel>Last error</MobileLabel>
                  {run.last_error ? (
                    <span
                      className="block min-w-0 truncate font-mono text-xs text-danger max-md:text-right"
                      title={run.last_error}
                    >
                      {run.last_error}
                    </span>
                  ) : (
                    <span className="text-subtle">—</span>
                  )}
                </td>
                <td className="max-md:block max-md:border-0 max-md:px-0 max-md:pt-3 max-md:pb-0">
                  <div className="flex flex-wrap items-center gap-1.5 md:justify-end">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={busy}
                      leadingIcon={<RefreshIcon size={14} />}
                      onClick={() => onRetry(run)}
                    >
                      Retry
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      className="text-danger hover:text-danger"
                      onClick={() => onAbandon(run)}
                    >
                      Abandon
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => onDetails(run)}>
                      Details
                    </Button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
