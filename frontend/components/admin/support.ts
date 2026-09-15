/**
 * Small helpers the operator console shares.
 *
 * Deriving the overview lives here rather than in a component so the counting
 * rules — which run is "the" run for a tenant, what counts as a failure — are
 * stated once and tested once.
 */

import { ApiError } from "@/lib/api";
import type { RunSummaryView } from "@/lib/types";

/** A refused or missing admin key. The console hands these upward, never renders them as "empty". */
export function isAuthError(caught: unknown): boolean {
  return caught instanceof ApiError && (caught.status === 401 || caught.status === 403);
}

export function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export interface OverviewCounts {
  tenants: number;
  active: number;
  inProgress: number;
  billing: number;
  failed: number;
}

/** Statuses that are not "still moving". Everything else is in progress. */
const SETTLED = new Set<string>(["active", "failed", "compensated", "billing_blocked"]);

/**
 * The newest run for each tenant.
 *
 * Relies on the API's ordering — `/admin/runs` returns newest first — so the
 * first run seen for a tenant is its current one. An older failed run that was
 * later retried into a new, active run must not count as a failure.
 */
export function latestRunPerTenant(runs: readonly RunSummaryView[]): RunSummaryView[] {
  const seen = new Set<string>();
  const latest: RunSummaryView[] = [];
  for (const run of runs) {
    if (seen.has(run.tenant_id)) continue;
    seen.add(run.tenant_id);
    latest.push(run);
  }
  return latest;
}

export function overviewCounts(runs: readonly RunSummaryView[]): OverviewCounts {
  const latest = latestRunPerTenant(runs);
  const count = (predicate: (run: RunSummaryView) => boolean) => latest.filter(predicate).length;
  return {
    tenants: latest.length,
    active: count((run) => run.status === "active"),
    inProgress: count((run) => !SETTLED.has(run.status)),
    billing: count((run) => run.status === "billing_blocked"),
    failed: count((run) => run.status === "failed" || run.status === "compensated"),
  };
}

/** Every known run for one tenant, newest first, from any number of fetched lists. */
export function runsForTenant(
  tenantId: string,
  ...sources: ReadonlyArray<readonly RunSummaryView[]>
): RunSummaryView[] {
  const seen = new Set<string>();
  const merged: RunSummaryView[] = [];
  for (const source of sources) {
    for (const run of source) {
      if (run.tenant_id !== tenantId || seen.has(run.run_id)) continue;
      seen.add(run.run_id);
      merged.push(run);
    }
  }
  return merged;
}
