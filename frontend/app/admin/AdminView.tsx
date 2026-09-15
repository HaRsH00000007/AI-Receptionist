"use client";

/**
 * The operator console.
 *
 * The reason the whole system exists, per the plan: when a run fails at 3am
 * someone sees it at 9am with the error in front of them and a retry button,
 * instead of discovering a week later that leads were dropped.
 *
 * Three deliberate choices, all about the fact that this page can spend money:
 *
 * **The admin key is held in component state, never persisted.** Not
 * localStorage, not a cookie this page can read. An XSS bug on an admin page
 * that stored its credential would hand an attacker the ability to retry and
 * abandon runs; one that does not store it hands them a page that must be
 * re-authenticated on every reload.
 *
 * **Destructive actions confirm, and name what they will do.** "Abandon"
 * releases a purchased phone number. The confirmation states that, because an
 * operator clicking through a generic "Are you sure?" has not been told
 * anything.
 *
 * **Retry is safe to press twice.** Every provisioning step is idempotent and
 * guarded by adoption checks, so a double-click cannot buy a second number.
 * That is a backend property; the UI does not add a second, weaker guard that
 * might drift from it.
 */

import { useEffect, useId, useMemo, useState } from "react";

import { AdminTopBar } from "@/components/admin/AdminTopBar";
import { OverviewStats } from "@/components/admin/OverviewStats";
import { RunsTable } from "@/components/admin/RunsTable";
import { errorMessage, isAuthError, runsForTenant } from "@/components/admin/support";
import { SystemHealth } from "@/components/admin/SystemHealth";
import { TenantDrawer } from "@/components/admin/TenantDrawer";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { EmptyState, Skeleton } from "@/components/ui/Feedback";
import { FilterIcon, RefreshIcon, ServerIcon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { abandonRun, listRuns, retryRun } from "@/lib/api";
import type { RunSummaryView } from "@/lib/types";

/** Statuses worth filtering to, in the order an operator cares about them. */
const FILTERS = [
  { value: "", label: "All runs" },
  { value: "failed", label: "Failed" },
  { value: "billing_blocked", label: "Waiting on billing" },
  { value: "active", label: "Active" },
] as const;

/** Wide enough to be a fair picture of the platform, small enough to load at once. */
const OVERVIEW_LIMIT = 200;

interface Props {
  adminKey: string;
  onUnauthorized: () => void;
  /** Drop the key and return to the gate. */
  onLock?: () => void;
}

export default function AdminView({ adminKey, onUnauthorized, onLock }: Props) {
  const [runs, setRuns] = useState<RunSummaryView[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyRunId, setBusyRunId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  //: Bumped to re-run the fetch effects, so the fetch logic lives in exactly
  //: one place rather than being duplicated into every caller.
  const [reloadNonce, setReloadNonce] = useState(0);

  const [allRuns, setAllRuns] = useState<RunSummaryView[] | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);

  const [abandonTarget, setAbandonTarget] = useState<RunSummaryView | null>(null);
  const [abandoning, setAbandoning] = useState(false);
  const [drawerTenant, setDrawerTenant] = useState<{ id: string; name: string } | null>(null);
  const filterId = useId();

  // The filtered table. Every state write sits behind an `await`, so there is
  // no synchronous setState in an effect body.
  useEffect(() => {
    let cancelled = false;

    async function refresh(): Promise<void> {
      try {
        const found = await listRuns(adminKey, { status: filter || undefined, limit: 100 });
        if (cancelled) return;
        setRuns(found);
        setError(null);
      } catch (caught) {
        if (cancelled) return;
        if (isAuthError(caught)) {
          onUnauthorized();
          return;
        }
        setError(errorMessage(caught, "Could not load runs."));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void refresh();
    return () => {
      cancelled = true;
    };
  }, [adminKey, filter, reloadNonce, onUnauthorized]);

  // The overview and tenant history, unfiltered — the table's filter must not
  // change the platform totals.
  useEffect(() => {
    let cancelled = false;

    async function refresh(): Promise<void> {
      try {
        const found = await listRuns(adminKey, { limit: OVERVIEW_LIMIT });
        if (cancelled) return;
        setAllRuns(found);
        setOverviewError(null);
      } catch (caught) {
        if (cancelled) return;
        if (isAuthError(caught)) {
          onUnauthorized();
          return;
        }
        setOverviewError(errorMessage(caught, "Could not load the overview."));
      }
    }

    void refresh();
    return () => {
      cancelled = true;
    };
  }, [adminKey, reloadNonce, onUnauthorized]);

  const tenantRuns = useMemo(
    () => (drawerTenant ? runsForTenant(drawerTenant.id, allRuns ?? [], runs) : []),
    [drawerTenant, allRuns, runs],
  );

  const reload = () => {
    setLoading(true);
    setReloadNonce((value) => value + 1);
  };

  async function retry(run: RunSummaryView): Promise<void> {
    setBusyRunId(run.run_id);
    setNotice(null);
    setError(null);
    try {
      const result = await retryRun(adminKey, run.run_id);
      setNotice(`Retry started for ${run.business_name}: ${result.detail}`);
      reload();
    } catch (caught) {
      if (isAuthError(caught)) {
        onUnauthorized();
        return;
      }
      setError(`Retry failed: ${errorMessage(caught, "could not retry that run.")}`);
    } finally {
      setBusyRunId(null);
    }
  }

  async function confirmAbandon(): Promise<void> {
    const run = abandonTarget;
    if (!run) return;
    setAbandoning(true);
    setBusyRunId(run.run_id);
    setNotice(null);
    setError(null);
    try {
      const result = await abandonRun(adminKey, run.run_id);
      setNotice(`Abandoned ${run.business_name}: ${result.detail}`);
      reload();
    } catch (caught) {
      if (isAuthError(caught)) {
        onUnauthorized();
        return;
      }
      setError(`Abandon failed: ${errorMessage(caught, "could not abandon that run.")}`);
    } finally {
      setAbandoning(false);
      setBusyRunId(null);
      setAbandonTarget(null);
    }
  }

  return (
    <div className="min-h-dvh">
      <AdminTopBar onLock={onLock} />

      <main id="main" className="container-page space-y-8 py-8 sm:py-10">
        <PageHeader
          title="Platform overview"
          description="Provisioning, receptionists and platform health across every tenant."
          actions={
            <Button variant="secondary" leadingIcon={<RefreshIcon size={16} />} onClick={reload}>
              Refresh
            </Button>
          }
        />

        {notice && (
          <Alert tone="success" live>
            {notice}
          </Alert>
        )}
        {error && <Alert tone="danger">{error}</Alert>}

        <OverviewStats runs={allRuns} error={overviewError} />

        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]">
          <Card className="min-w-0 overflow-hidden">
            <CardHeader
              title="Provisioning runs"
              description="Newest first. Retry is safe to repeat; abandoning releases what a run bought."
              action={
                <div className="flex items-center gap-2">
                  <FilterIcon size={16} className="text-subtle max-sm:hidden" />
                  <label htmlFor={filterId} className="sr-only">
                    Filter by status
                  </label>
                  <select
                    id={filterId}
                    value={filter}
                    onChange={(event) => {
                      setLoading(true);
                      setFilter(event.target.value);
                    }}
                    className="field mt-0 h-9 min-h-9 w-auto py-1 sm:text-sm"
                  >
                    {FILTERS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
              }
            />

            {loading ? (
              <div className="space-y-3 p-5" aria-busy="true">
                <p role="status" className="sr-only">
                  Loading runs…
                </p>
                {[0, 1, 2, 3].map((index) => (
                  <Skeleton key={index} className="h-12 w-full" />
                ))}
              </div>
            ) : runs.length === 0 ? (
              <EmptyState
                className="m-5"
                icon={<ServerIcon />}
                title="Nothing matches that filter."
                description={
                  filter ? "Try a different status." : "No provisioning runs have been recorded yet."
                }
              />
            ) : (
              <RunsTable
                runs={runs}
                busyRunId={busyRunId}
                onRetry={(run) => void retry(run)}
                onAbandon={setAbandonTarget}
                onDetails={(run) => setDrawerTenant({ id: run.tenant_id, name: run.business_name })}
              />
            )}
          </Card>

          <SystemHealth refreshKey={reloadNonce} />
        </div>
      </main>

      <ConfirmDialog
        open={abandonTarget !== null}
        title={`Abandon provisioning for ${abandonTarget?.business_name ?? ""}?`}
        confirmLabel="Abandon run"
        busy={abandoning}
        onConfirm={() => void confirmAbandon()}
        onCancel={() => setAbandonTarget(null)}
      >
        <p>
          This releases any phone number already purchased, deletes the voice agent and marks the
          run compensated. {abandonTarget?.business_name} will not have a working receptionist.
        </p>
      </ConfirmDialog>

      {drawerTenant && (
        <TenantDrawer
          key={drawerTenant.id}
          adminKey={adminKey}
          tenant={drawerTenant}
          runs={tenantRuns}
          onClose={() => setDrawerTenant(null)}
          onUnauthorized={onUnauthorized}
        />
      )}
    </div>
  );
}
