"use client";

/**
 * Platform readiness, from `GET /readyz`.
 *
 * Deliberately separate from the admin-key calls: readiness is unauthenticated,
 * and a console that could not reach the API should still say so here rather
 * than looking like a console with nothing to report.
 */

import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Feedback";
import { RefreshIcon } from "@/components/ui/icons";
import { getReadiness } from "@/lib/api";
import { formatTime, humanize } from "@/lib/format";
import type { ReadinessView } from "@/lib/types";

import { errorMessage } from "./support";

interface HealthState {
  data: ReadinessView | null;
  error: string | null;
  checkedAt: string | null;
}

export function SystemHealth({ refreshKey }: { refreshKey: number }) {
  const [state, setState] = useState<HealthState>({ data: null, error: null, checkedAt: null });
  const [nonce, setNonce] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function check(): Promise<void> {
      try {
        const data = await getReadiness();
        if (cancelled) return;
        setState({ data, error: null, checkedAt: new Date().toISOString() });
      } catch (caught) {
        if (cancelled) return;
        setState((previous) => ({
          data: previous.data,
          error: errorMessage(caught, "The readiness endpoint did not answer."),
          checkedAt: new Date().toISOString(),
        }));
      } finally {
        if (!cancelled) setRefreshing(false);
      }
    }

    void check();
    return () => {
      cancelled = true;
    };
  }, [refreshKey, nonce]);

  const { data, error, checkedAt } = state;
  const loaded = data !== null || error !== null;
  const checks = data ? Object.entries(data.checks) : [];

  return (
    <Card className="overflow-hidden">
      <CardHeader
        title="System health"
        description={checkedAt ? `Checked at ${formatTime(checkedAt)}` : "API readiness and its dependencies"}
        action={
          <Button
            variant="ghost"
            size="sm"
            iconOnly
            aria-label="Refresh health"
            loading={refreshing}
            onClick={() => {
              setRefreshing(true);
              setNonce((value) => value + 1);
            }}
          >
            {!refreshing && <RefreshIcon size={15} />}
          </Button>
        }
      />
      <div className="space-y-4 p-5">
        {!loaded && (
          <div className="space-y-3" aria-hidden>
            <Skeleton className="h-6 w-24" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        )}

        {error && (
          <Alert tone="warn" title="Readiness check unavailable">
            {error}
          </Alert>
        )}

        {data && (
          <>
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm text-muted">Overall</span>
              <Badge tone={data.status === "ready" ? "success" : "danger"} dot>
                {data.status === "ready" ? "Ready" : "Not ready"}
              </Badge>
            </div>

            {checks.length === 0 ? (
              <p className="text-sm text-muted">No dependency checks are registered.</p>
            ) : (
              <ul className="divide-y divide-line rounded-xl border border-line">
                {checks.map(([name, check]) => (
                  <li key={name} className="flex items-start justify-between gap-3 px-3.5 py-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">
                        {humanize(name)}
                        {check.critical && (
                          <span className="ml-1.5 text-[0.6875rem] font-semibold uppercase tracking-wide text-subtle">
                            Critical
                          </span>
                        )}
                      </p>
                      {check.detail !== null && check.detail !== "" && (
                        <p className="mt-0.5 break-words text-xs text-muted">{String(check.detail)}</p>
                      )}
                    </div>
                    <Badge tone={check.ok ? "success" : check.critical ? "danger" : "warn"}>
                      {check.ok ? "OK" : "Failing"}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}

            {data.degraded.length > 0 && (
              <Alert tone="warn" title="Degraded">
                Serving, but {data.degraded.map(humanize).join(", ")}{" "}
                {data.degraded.length === 1 ? "is" : "are"} not healthy.
              </Alert>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
