"use client";

/**
 * One tenant, as far as the admin API can see it.
 *
 * That is: its provisioning runs, and the full history of its agent
 * configuration — with the two operations that act on it, rollback and
 * resync. Rollback only moves the "live" flag; resync is what pushes the live
 * prompt to the voice vendor. They are separate on the backend on purpose (a
 * rollback must work while the vendor is down), so they are separate here too,
 * and the confirmation says so.
 */

import { useEffect, useState, type ReactNode } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { CopyButton } from "@/components/ui/CopyButton";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { Skeleton } from "@/components/ui/Feedback";
import { RefreshIcon } from "@/components/ui/icons";
import { getConfigDetail, listConfigs, resyncAgent, rollbackConfig } from "@/lib/api";
import { formatDateTime, humanize } from "@/lib/format";
import { friendlyError, stepLabel } from "@/lib/status";
import type { AgentConfigDetailView, AgentConfigView, RunSummaryView } from "@/lib/types";

import { RunStatusBadge } from "./RunStatusBadge";
import { errorMessage, isAuthError } from "./support";

function SectionTitle({ id, children }: { id: string; children: ReactNode }) {
  return (
    <h3 id={id} className="text-xs font-semibold uppercase tracking-[0.08em] text-subtle">
      {children}
    </h3>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-subtle">{label}</dt>
      <dd className="mt-0.5 break-words text-sm text-ink-2">{children}</dd>
    </div>
  );
}

export function TenantDrawer({
  adminKey,
  tenant,
  runs,
  onClose,
  onUnauthorized,
}: {
  adminKey: string;
  tenant: { id: string; name: string };
  /** This tenant's runs, newest first. */
  runs: readonly RunSummaryView[];
  onClose: () => void;
  onUnauthorized: () => void;
}) {
  const [configs, setConfigs] = useState<AgentConfigView[] | null>(null);
  const [configsError, setConfigsError] = useState<string | null>(null);
  const [configNonce, setConfigNonce] = useState(0);

  const [openVersion, setOpenVersion] = useState<number | null>(null);
  const [details, setDetails] = useState<Record<number, AgentConfigDetailView>>({});
  const [loadingVersion, setLoadingVersion] = useState<number | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [rollbackTarget, setRollbackTarget] = useState<number | null>(null);
  const [busy, setBusy] = useState<"rollback" | "resync" | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load(): Promise<void> {
      try {
        const found = await listConfigs(adminKey, tenant.id);
        if (cancelled) return;
        setConfigs(found);
        setConfigsError(null);
      } catch (caught) {
        if (cancelled) return;
        if (isAuthError(caught)) {
          onUnauthorized();
          return;
        }
        setConfigsError(errorMessage(caught, "Could not load configuration versions."));
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [adminKey, tenant.id, configNonce, onUnauthorized]);

  async function togglePrompt(version: number): Promise<void> {
    if (openVersion === version) {
      setOpenVersion(null);
      return;
    }
    setOpenVersion(version);
    setDetailError(null);
    if (details[version]) return;

    setLoadingVersion(version);
    try {
      const detail = await getConfigDetail(adminKey, tenant.id, version);
      setDetails((previous) => ({ ...previous, [version]: detail }));
    } catch (caught) {
      if (isAuthError(caught)) {
        onUnauthorized();
        return;
      }
      setDetailError(errorMessage(caught, "Could not load that version."));
    } finally {
      setLoadingVersion(null);
    }
  }

  async function confirmRollback(): Promise<void> {
    if (rollbackTarget === null) return;
    const version = rollbackTarget;
    setBusy("rollback");
    setActionError(null);
    setActionNotice(null);
    try {
      const result = await rollbackConfig(adminKey, tenant.id, version);
      setActionNotice(result.detail);
      setOpenVersion(null);
      setConfigNonce((value) => value + 1);
    } catch (caught) {
      if (isAuthError(caught)) {
        onUnauthorized();
        return;
      }
      setActionError(`Rollback failed: ${errorMessage(caught, "the request did not complete.")}`);
    } finally {
      setBusy(null);
      setRollbackTarget(null);
    }
  }

  async function resync(): Promise<void> {
    setBusy("resync");
    setActionError(null);
    setActionNotice(null);
    try {
      const result = await resyncAgent(adminKey, tenant.id);
      setActionNotice(result.detail);
      setConfigNonce((value) => value + 1);
    } catch (caught) {
      if (isAuthError(caught)) {
        onUnauthorized();
        return;
      }
      setActionError(`Resync failed: ${errorMessage(caught, "the request did not complete.")}`);
    } finally {
      setBusy(null);
    }
  }

  const latest = runs[0];
  const latestError = latest?.last_error ? friendlyError(latest.last_error) : null;

  return (
    <>
      <Dialog
        open
        variant="drawer"
        title={tenant.name}
        description="Tenant details"
        // While the rollback confirmation is open, Escape belongs to it alone.
        onClose={() => {
          if (rollbackTarget === null) onClose();
        }}
      >
        <div className="space-y-8">
          <section aria-labelledby="tenant-identity">
            <SectionTitle id="tenant-identity">Identity</SectionTitle>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <code className="break-all rounded-md bg-surface-2 px-2 py-1 font-mono text-xs text-ink-2">
                {tenant.id}
              </code>
              <CopyButton value={tenant.id} label="Copy ID" />
            </div>
          </section>

          {latest && (
            <section aria-labelledby="tenant-latest">
              <SectionTitle id="tenant-latest">Latest provisioning run</SectionTitle>
              <div className="mt-3 rounded-xl border border-line p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <RunStatusBadge status={latest.status} />
                  <span className="text-sm text-muted">
                    {latest.current_step ? stepLabel(latest.current_step) : "No step in progress"}
                  </span>
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-3">
                  <Detail label="Attempt">{latest.attempt}</Detail>
                  <Detail label="Run">
                    <span className="font-mono text-xs">{latest.run_id.slice(0, 8)}</span>
                  </Detail>
                  <Detail label="Started">{formatDateTime(latest.started_at)}</Detail>
                  <Detail label="Finished">{formatDateTime(latest.finished_at)}</Detail>
                </dl>
                {latestError && (
                  <div className="mt-4 rounded-lg border border-danger-line bg-danger-soft p-3 text-danger-ink">
                    <p className="text-sm font-semibold">{latestError.title}</p>
                    <p className="mt-1 text-sm">{latestError.explanation}</p>
                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs font-medium">Technical details</summary>
                      <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-xs">
                        {latestError.technical}
                      </pre>
                    </details>
                  </div>
                )}
              </div>
            </section>
          )}

          <section aria-labelledby="tenant-history">
            <SectionTitle id="tenant-history">Provisioning history</SectionTitle>
            {runs.length === 0 ? (
              <p className="mt-3 text-sm text-muted">No runs for this tenant in the loaded list.</p>
            ) : (
              <ol className="mt-3 divide-y divide-line rounded-xl border border-line">
                {runs.map((run) => (
                  <li key={run.run_id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 px-4 py-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">
                        {run.current_step ? stepLabel(run.current_step) : "No step in progress"}
                      </p>
                      <p className="mt-0.5 text-xs text-muted">
                        Started {formatDateTime(run.started_at)} · attempt {run.attempt} ·{" "}
                        <span className="font-mono">{run.run_id.slice(0, 8)}</span>
                      </p>
                    </div>
                    <RunStatusBadge status={run.status} />
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section aria-labelledby="tenant-configs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <SectionTitle id="tenant-configs">Configuration versions</SectionTitle>
              <Button
                size="sm"
                variant="secondary"
                loading={busy === "resync"}
                disabled={busy !== null}
                leadingIcon={<RefreshIcon size={14} />}
                onClick={() => void resync()}
              >
                Resync agent
              </Button>
            </div>
            <p className="mt-1.5 text-xs text-muted">
              Resync pushes the live version to the voice vendor. It is refused for tenants served
              by a shared vertical agent.
            </p>

            <div className="mt-3 space-y-3">
              {actionNotice && (
                <Alert tone="success" live>
                  {actionNotice}
                </Alert>
              )}
              {actionError && <Alert tone="danger">{actionError}</Alert>}

              {configsError ? (
                <Alert tone="warn" title="Configuration history unavailable">
                  {configsError}
                </Alert>
              ) : configs === null ? (
                <div className="space-y-2" aria-hidden>
                  <Skeleton className="h-20 w-full" />
                  <Skeleton className="h-20 w-full" />
                </div>
              ) : configs.length === 0 ? (
                <p className="text-sm text-muted">No configuration has been generated yet.</p>
              ) : (
                <ul className="space-y-2.5">
                  {configs.map((config) => {
                    const expanded = openVersion === config.version;
                    const detail = details[config.version];
                    return (
                      <li key={config.version} className="rounded-xl border border-line p-4">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="font-mono text-sm font-semibold">{`v${config.version}`}</span>
                          {config.is_live && (
                            <Badge tone="success" dot>
                              Live
                            </Badge>
                          )}
                          <span className="text-xs text-muted">
                            {humanize(config.generated_by)}
                            {config.template_version && ` · template ${config.template_version}`}
                          </span>
                          <span className="text-xs text-subtle sm:ml-auto">
                            {formatDateTime(config.created_at)}
                          </span>
                        </div>
                        {config.generator_detail && (
                          <p className="mt-1 break-words text-xs text-muted">{config.generator_detail}</p>
                        )}
                        <div className="mt-3 flex flex-wrap gap-2">
                          <Button
                            size="sm"
                            variant="secondary"
                            aria-expanded={expanded}
                            loading={loadingVersion === config.version}
                            onClick={() => void togglePrompt(config.version)}
                          >
                            {expanded ? "Hide prompt" : "View prompt"}
                          </Button>
                          {!config.is_live && (
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={busy !== null}
                              onClick={() => setRollbackTarget(config.version)}
                            >
                              Make live
                            </Button>
                          )}
                        </div>

                        {expanded && detailError && (
                          <Alert tone="warn" className="mt-3">
                            {detailError}
                          </Alert>
                        )}
                        {expanded && detail && (
                          <div className="mt-3 space-y-3">
                            <div>
                              <p className="text-xs font-medium text-subtle">First message</p>
                              <p className="mt-1 rounded-lg bg-surface-2 p-3 text-sm">{detail.first_message}</p>
                            </div>
                            <div>
                              <p className="text-xs font-medium text-subtle">System prompt</p>
                              <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-3 font-mono text-xs leading-relaxed text-ink-2">
                                {detail.system_prompt}
                              </pre>
                            </div>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </section>

          <p className="border-t border-line pt-4 text-xs text-subtle">
            Not available from the admin API: this tenant&apos;s phone number, voice agent and
            usage. Those are readable only with a session that belongs to the tenant.
          </p>
        </div>
      </Dialog>

      <ConfirmDialog
        open={rollbackTarget !== null}
        title={`Make version ${rollbackTarget ?? ""} live?`}
        confirmLabel={`Make v${rollbackTarget ?? ""} live`}
        tone="primary"
        busy={busy === "rollback"}
        onConfirm={() => void confirmRollback()}
        onCancel={() => setRollbackTarget(null)}
      >
        <p>
          Version {rollbackTarget} becomes the live configuration for {tenant.name} immediately. The
          voice vendor keeps serving the previous prompt until you run{" "}
          <strong className="font-semibold text-ink">Resync agent</strong>.
        </p>
      </ConfirmDialog>
    </>
  );
}
