"use client";

/**
 * Activation: the provisioning run, live.
 *
 * Polls until the run reaches a terminal state, then stops. All steps are shown
 * from the first render — the backend writes them at signup — so the page
 * answers "how far along is this, and what failed" rather than a spinner.
 *
 * A failure is explained in words first. The raw error is still available
 * behind "Technical details", because a support conversation needs it; it is
 * never a stack trace, only the backend's `[code] message`.
 */

import { useEffect, useState, type ReactNode } from "react";

import { ForwardingGuide } from "@/components/app/ForwardingGuide";
import { ProvisioningTimeline } from "@/components/app/ProvisioningTimeline";
import { SupportButton } from "@/components/app/SupportButton";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { CopyButton } from "@/components/ui/CopyButton";
import { cx } from "@/components/ui/cx";
import { EmptyState, ProgressBar, Skeleton } from "@/components/ui/Feedback";
import {
  AlertTriangleIcon,
  CheckIcon,
  PhoneIcon,
  PhoneIncomingIcon,
  XIcon,
} from "@/components/ui/icons";
import { ApiError, getPhoneOrNull, getProvisioning, getTenant, listCalls } from "@/lib/api";
import { formatPhone } from "@/lib/format";
import { billingReason, friendlyError, isUrgent, stepLabel } from "@/lib/status";
import {
  businessTypeLabel,
  isTerminal,
  type CallView,
  type PhoneNumberView,
  type ProvisioningView,
  type TenantView,
} from "@/lib/types";

import { OnboardingStepper } from "./OnboardingStepper";

const POLL_INTERVAL_MS = 2_000;

interface Snapshot {
  tenant: TenantView;
  provisioning: ProvisioningView;
  phone: PhoneNumberView | null;
  calls: CallView[];
}

type Phase = "running" | "live" | "billing" | "failed";

function phaseOf(provisioning: ProvisioningView): Phase {
  if (provisioning.status === "active") return "live";
  if (provisioning.status === "billing_blocked") return "billing";
  if (provisioning.status === "failed" || provisioning.status === "compensated") return "failed";
  return "running";
}

export function StatusView({
  tenantId,
  statusToken,
  forwarding = false,
}: {
  tenantId: string;
  /**
   * The signed grant from the signup response. Without it the API refuses the
   * read — a tenant id on its own is not a credential.
   */
  statusToken?: string;
  /** The business chose to keep its existing number and forward it. */
  forwarding?: boolean;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const status = snapshot?.provisioning.status;
  const settled = status !== undefined && isTerminal(status);

  useEffect(() => {
    // One effect owns the whole polling lifecycle: fetch now, then on an
    // interval, and stop entirely once the run reaches a terminal state. A page
    // left open overnight should not keep hitting the API.
    let cancelled = false;

    async function refresh() {
      try {
        const [tenant, provisioning, phone, calls] = await Promise.all([
          getTenant(tenantId, statusToken),
          getProvisioning(tenantId, statusToken),
          getPhoneOrNull(tenantId, statusToken),
          listCalls(tenantId, statusToken),
        ]);
        if (cancelled) return;
        setSnapshot({ tenant, provisioning, phone, calls });
        setError(null);
      } catch (caught) {
        if (cancelled) return;
        setError(caught instanceof ApiError ? caught : new ApiError("Could not load status.", { status: 0 }));
      }
    }

    void refresh();
    if (settled) {
      return () => {
        cancelled = true;
      };
    }
    const timer = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [tenantId, statusToken, settled]);

  if (error && !snapshot) {
    return (
      <Frame>
        <Alert tone="danger" title="We couldn't load your setup status">
          <p>{error.message}</p>
          {error.correlationId && (
            <p className="mt-1 font-mono text-xs opacity-80">Reference: {error.correlationId}</p>
          )}
        </Alert>
        <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted">
            Status links expire after a while. If your receptionist is already set up, sign in to see it.
          </p>
          <ButtonLink href="/login" variant="secondary">
            Sign in
          </ButtonLink>
        </Card>
      </Frame>
    );
  }

  if (!snapshot) {
    return (
      <Frame>
        <div aria-busy="true" className="space-y-6">
          <p role="status" className="sr-only">
            Loading setup status…
          </p>
          <Skeleton className="h-48 rounded-2xl" />
          <Skeleton className="h-96 rounded-2xl" />
        </div>
      </Frame>
    );
  }

  const { tenant, provisioning, phone, calls } = snapshot;
  const phase = phaseOf(provisioning);

  return (
    <Frame complete={phase === "live"}>
      <StatusHero phase={phase} tenant={tenant} provisioning={provisioning} phone={phone} />

      {phase === "live" && forwarding && phone && <ForwardingGuide number={phone.e164} />}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <Card className="min-w-0">
          <CardHeader
            title="Setup progress"
            description={`${provisioning.completed_steps} of ${provisioning.total_steps} steps complete${settled ? "" : " · updating"}`}
            action={
              phase === "live" ? (
                <Badge tone="success" dot>
                  Complete
                </Badge>
              ) : phase === "failed" ? (
                <Badge tone="danger">Stopped</Badge>
              ) : phase === "billing" ? (
                <Badge tone="warn">Paused</Badge>
              ) : (
                <Badge tone="accent" dot pulse>
                  Live
                </Badge>
              )
            }
          />
          <div className="px-5 py-6 sm:px-6">
            <ProgressBar
              value={provisioning.completed_steps}
              max={provisioning.total_steps}
              label="Setup progress"
              tone={phase === "live" ? "success" : phase === "failed" ? "danger" : phase === "billing" ? "warn" : "accent"}
              className="mb-7"
            />
            <ProvisioningTimeline provisioning={provisioning} />
          </div>
        </Card>

        <aside className="space-y-6">
          <Card className="p-5">
            <p className="text-xs font-semibold tracking-[0.06em] text-muted uppercase">Your business</p>
            <p className="mt-2 text-lg font-bold tracking-tight">{tenant.name}</p>
            <dl className="mt-3 space-y-2 text-sm">
              <Fact label="Type">{businessTypeLabel(tenant.business_type)}</Fact>
              <Fact label="Area code">{tenant.area_code ?? "—"}</Fact>
              <Fact label="Timezone">{tenant.timezone}</Fact>
            </dl>
          </Card>
          <Card className="p-5">
            <p className="text-sm font-semibold">What happens next</p>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              {phase === "live"
                ? "Call your number to hear your receptionist. Sign in to your dashboard to see calls, summaries and usage."
                : phase === "failed"
                  ? "Our team can see exactly where setup stopped and can retry it safely. Contact us and quote the reference shown above."
                  : phase === "billing"
                    ? "Setup picks up automatically once your subscription is active. There's nothing to resubmit."
                    : "You can close this page. Setup keeps running, and we'll email you when your receptionist is live."}
            </p>
            {phase === "live" && (
              <ButtonLink href="/login" variant="secondary" block className="mt-4">
                Sign in to your dashboard
              </ButtonLink>
            )}
          </Card>
        </aside>
      </div>

      {phase === "live" && <RecentCalls calls={calls} />}
    </Frame>
  );
}

function Frame({ complete = false, children }: { complete?: boolean; children: ReactNode }) {
  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <OnboardingStepper current={4} complete={complete} />
      {children}
    </div>
  );
}

function StatusHero({
  phase,
  tenant,
  provisioning,
  phone,
}: {
  phase: Phase;
  tenant: TenantView;
  provisioning: ProvisioningView;
  phone: PhoneNumberView | null;
}) {
  const running = provisioning.steps.find((step) => step.status === "running");
  const next = running ?? provisioning.steps.find((step) => step.status === "pending");
  const friendly = friendlyError(provisioning.last_error);

  return (
    <section
      aria-labelledby="status-heading"
      role={phase === "failed" ? "alert" : undefined}
      className={cx(
        "relative overflow-hidden rounded-2xl border p-6 shadow-sm sm:p-10",
        phase === "live" && "border-success-line bg-success-soft",
        phase === "failed" && "border-danger-line bg-danger-soft",
        phase === "billing" && "border-warn-line bg-warn-soft",
        phase === "running" && "border-line bg-surface",
      )}
    >
      {phase === "running" && <div aria-hidden className="grid-backdrop absolute inset-0 opacity-70" />}
      <div className="relative flex flex-col gap-7 sm:flex-row sm:items-center sm:gap-10">
        <Orb phase={phase} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-muted">{tenant.name}</p>

          {phase === "running" && (
            <>
              <h1 id="status-heading" className="heading-lg mt-1.5">
                We&apos;re setting up your receptionist
              </h1>
              <p className="mt-2.5 max-w-2xl text-[0.9375rem] leading-relaxed text-muted">
                {next ? `Now: ${stepLabel(next.step_name)}. ` : ""}This usually takes a few minutes. You
                can close this page; setup keeps running.
              </p>
            </>
          )}

          {phase === "live" && (
            <>
              <h1 id="status-heading" className="heading-lg mt-1.5 text-success-ink">
                Your receptionist is live
              </h1>
              {phone && (
                <p className="mt-3 text-[2rem] leading-tight font-bold tracking-tight text-success-ink tabular-nums sm:text-[2.75rem]">
                  {formatPhone(phone.e164)}
                </p>
              )}
              <p className="mt-2.5 max-w-2xl text-[0.9375rem] leading-relaxed text-success-ink">
                Call it to hear your receptionist answer. Every call shows up with a summary.
              </p>
              {phone && (
                <div className="mt-6 flex flex-wrap gap-2.5">
                  <ButtonLink href={`tel:${phone.e164}`} leadingIcon={<PhoneIcon size={16} />}>
                    Call it now
                  </ButtonLink>
                  <CopyButton value={phone.e164} label="Copy number" size="md" />
                </div>
              )}
            </>
          )}

          {phase === "billing" && (
            <>
              <h1 id="status-heading" className="heading-lg mt-1.5 text-warn-ink">
                Waiting for your subscription
              </h1>
              <p className="mt-2.5 max-w-2xl text-[0.9375rem] leading-relaxed text-warn-ink">
                {billingReason(provisioning.billing?.reason)}
              </p>
              <p className="mt-2 max-w-2xl text-[0.9375rem] leading-relaxed text-warn-ink">
                Setup continues automatically once payment is confirmed. There&apos;s nothing to resubmit.
              </p>
            </>
          )}

          {phase === "failed" && (
            <>
              <h1 id="status-heading" className="heading-lg mt-1.5 text-danger-ink">
                Setup did not complete
              </h1>
              <p className="mt-2.5 text-[0.9375rem] font-semibold text-danger-ink">{friendly.title}</p>
              <p className="mt-1 max-w-2xl text-[0.9375rem] leading-relaxed text-danger-ink">
                {friendly.explanation} Nothing is being charged for this setup.
              </p>
              <p className="mt-3 text-sm text-danger-ink">
                Reference <span className="font-mono">{provisioning.correlation_id}</span>
              </p>
              {friendly.technical && (
                <details className="mt-3 text-sm text-danger-ink">
                  <summary className="cursor-pointer font-medium">Technical details</summary>
                  <p className="mt-2 rounded-lg bg-danger-line/30 p-3 font-mono text-xs break-words">
                    {friendly.technical}
                  </p>
                </details>
              )}
              <div className="mt-6">
                <SupportButton />
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function Orb({ phase }: { phase: Phase }) {
  const fill = { running: "bg-accent", live: "bg-success", billing: "bg-warn", failed: "bg-danger" }[phase];
  const halo = phase === "live" ? "bg-success/20" : "bg-accent/20";
  return (
    <div aria-hidden className="relative flex size-24 shrink-0 items-center justify-center sm:size-28">
      {(phase === "running" || phase === "live") && (
        <>
          <span className={cx("absolute inset-0 rounded-full [animation:ring-pulse_2.4s_ease-out_infinite]", halo)} />
          <span
            className={cx("absolute inset-0 rounded-full [animation:ring-pulse_2.4s_ease-out_1.2s_infinite]", halo)}
          />
        </>
      )}
      <span
        className={cx(
          "relative flex size-20 items-center justify-center rounded-full text-bg shadow-lg sm:size-24",
          fill,
        )}
      >
        {phase === "running" && (
          <span className="waveform h-8">
            {[12, 22, 30, 18, 26, 14].map((height, index) => (
              <span key={index} style={{ height }} />
            ))}
          </span>
        )}
        {phase === "live" && <CheckIcon size={36} strokeWidth={2.6} />}
        {phase === "billing" && <AlertTriangleIcon size={34} />}
        {phase === "failed" && <XIcon size={36} strokeWidth={2.6} />}
      </span>
    </div>
  );
}

function RecentCalls({ calls }: { calls: CallView[] }) {
  return (
    <Card className="overflow-hidden">
      <CardHeader title="Recent calls" description="Calls appear here moments after they end." />
      {calls.length === 0 ? (
        <div className="p-5">
          <EmptyState
            icon={<PhoneIncomingIcon />}
            title="No calls yet"
            description="Ring your number and the call will show up here with a summary."
          />
        </div>
      ) : (
        <ul className="divide-y divide-line">
          {calls.map((call) => (
            <li key={call.id} className="px-5 py-4 sm:px-6">
              <div className="flex items-baseline justify-between gap-3">
                <p className="min-w-0 text-sm font-semibold">
                  {call.caller_name ?? "Unknown caller"}
                  {call.from_e164 && (
                    <span className="ml-2 text-xs font-normal text-muted tabular-nums">
                      {formatPhone(call.from_e164)}
                    </span>
                  )}
                </p>
                {isUrgent(call.urgency) && <Badge tone="warn">Urgent</Badge>}
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
                {call.summary ?? "Summary not available for this call."}
              </p>
              {call.callback_number && (
                <p className="mt-2 text-xs text-muted">
                  Callback: <span className="tabular-nums">{formatPhone(call.callback_number)}</span> (as
                  stated by the caller)
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right font-medium text-ink">{children}</dd>
    </div>
  );
}
