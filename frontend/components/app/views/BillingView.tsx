"use client";

/**
 * The plan and the subscription state the billing gate reports.
 *
 * Entitlement comes from the provisioning read, which evaluates it server-side
 * through the same function the money gate uses — so what this page says and
 * what the gate decides cannot disagree. There is no payment-method or invoice
 * API, and the page says so instead of drawing controls for one.
 */

import { SupportButton } from "@/components/app/SupportButton";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { cx } from "@/components/ui/cx";
import { CheckIcon, InfoIcon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { formatDate, formatNumber, humanize } from "@/lib/format";
import {
  ALL_FEATURES,
  FEATURE_LABELS,
  PLAN_CATALOG,
  TRIAL_INCLUDED_MINUTES,
  formatPrice,
  planById,
  planName,
} from "@/lib/plans";
import { billingReason } from "@/lib/status";
import type { Tone } from "@/lib/tone";

const SUBSCRIPTION: Record<string, { label: string; tone: Tone }> = {
  active: { label: "Active", tone: "success" },
  trialing: { label: "Free trial", tone: "accent" },
  past_due: { label: "Payment past due", tone: "warn" },
  canceled: { label: "Canceled", tone: "danger" },
  expired: { label: "Trial expired", tone: "danger" },
};

export function BillingView() {
  const { tenant, provisioning, usage } = useTenantData();
  const billing = provisioning.billing;
  const planId = billing?.plan ?? tenant.plan;
  const plan = planById(planId);
  const subscription = billing?.status
    ? (SUBSCRIPTION[billing.status] ?? { label: humanize(billing.status), tone: "neutral" as const })
    : null;

  return (
    <div className="space-y-6 lg:space-y-8">
      <PageHeader title="Billing" description="Your plan and the state of your subscription." />

      {billing && !billing.entitled && (
        <Alert tone="warn" title="Your subscription needs attention">
          {billingReason(billing.reason)}
        </Alert>
      )}

      <Card className="overflow-hidden">
        <div className="grid gap-6 p-6 sm:p-8 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-muted">Current plan</p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <p className="text-3xl font-bold tracking-tight">{planName(planId)}</p>
              {subscription && (
                <Badge tone={subscription.tone} dot>
                  {subscription.label}
                </Badge>
              )}
            </div>
            <p className="mt-2 text-sm text-muted">
              {plan?.summary ??
                (planId === "trial" ? `Your free trial includes ${TRIAL_INCLUDED_MINUTES} minutes.` : "")}
            </p>
            {billing?.trial_ends_at && (
              <p className="mt-2 text-sm font-medium text-ink-2">
                Trial ends {formatDate(billing.trial_ends_at)}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-2.5 md:items-end">
            <p className="text-sm text-muted">
              {plan?.price ? formatPrice(plan.price) : "Pricing is arranged with our team"}
            </p>
            <SupportButton label="Talk to us about your plan" />
          </div>
        </div>
        <dl className="grid gap-px border-t border-line bg-line sm:grid-cols-3">
          <Fact label="Included minutes" value={`${formatNumber(usage.included_minutes)} per period`} />
          <Fact label="Used this period" value={`${formatNumber(usage.call_minutes)} minutes`} />
          <Fact
            label="At the limit"
            value={usage.blocks_on_overage ? "Calls stop being answered" : "Extra minutes billed at your plan rate"}
          />
        </dl>
      </Card>

      <section aria-labelledby="plans-heading">
        <h2 id="plans-heading" className="text-lg font-bold tracking-tight">
          Plans
        </h2>
        <p className="mt-1 text-sm text-muted">What each plan includes. Contact us to change plans.</p>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          {PLAN_CATALOG.map((entry) => {
            const current = entry.id === planId;
            return (
              <div
                key={entry.id}
                className={cx("card flex flex-col p-5", current && "border-accent shadow-[inset_0_0_0_1px_var(--accent)]")}
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="text-base font-bold">{entry.name}</p>
                  {current && <Badge tone="accent">Current plan</Badge>}
                </div>
                <p className="mt-1 text-sm text-muted">{entry.summary}</p>
                <p className="mt-4 text-xl font-bold tracking-tight">
                  {entry.price ? formatPrice(entry.price) : "Custom pricing"}
                </p>
                <ul className="mt-4 space-y-2 border-t border-line pt-4 text-sm">
                  <li className="flex items-center gap-2.5">
                    <CheckIcon size={15} className="shrink-0 text-success" />
                    {formatNumber(entry.includedMinutes)} minutes included
                  </li>
                  {ALL_FEATURES.filter((feature) => entry.features.includes(feature)).map((feature) => (
                    <li key={feature} className="flex items-center gap-2.5">
                      <CheckIcon size={15} className="shrink-0 text-success" />
                      {FEATURE_LABELS[feature]}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </section>

      <div className="flex gap-3 rounded-xl border border-line bg-surface px-5 py-4 text-sm text-muted">
        <InfoIcon size={17} className="mt-0.5 shrink-0" />
        <p>
          Payment methods and invoices aren&apos;t managed in the dashboard yet. For anything
          billing-related, contact our team.
        </p>
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface px-6 py-4">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 text-sm font-semibold text-ink">{value}</dd>
    </div>
  );
}
