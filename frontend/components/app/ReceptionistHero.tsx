/**
 * The answer to "how is my receptionist doing?", in one card.
 */

import { Badge } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { cx } from "@/components/ui/cx";
import { AlertTriangleIcon, PhoneIcon, SlidersIcon, XCircleIcon } from "@/components/ui/icons";
import { formatPhone } from "@/lib/format";
import {
  RECEPTIONIST_STATE,
  receptionistState,
  stepLabel,
  type ReceptionistState,
} from "@/lib/status";
import type { PhoneNumberView, ProvisioningView, TenantView } from "@/lib/types";

export function ReceptionistHero({
  tenant,
  provisioning,
  phone,
  showConfigurationLink = true,
}: {
  tenant: TenantView;
  provisioning: ProvisioningView;
  phone: PhoneNumberView | null;
  showConfigurationLink?: boolean;
}) {
  const state = receptionistState(tenant.status, provisioning.status);
  const meta = RECEPTIONIST_STATE[state];

  return (
    <section
      aria-labelledby="receptionist-hero-heading"
      className="relative overflow-hidden rounded-2xl border border-line bg-surface shadow-sm"
    >
      <div
        aria-hidden
        className="grid-backdrop pointer-events-none absolute inset-0 opacity-60"
      />
      <div className="relative grid gap-8 p-6 sm:p-8 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h2 id="receptionist-hero-heading" className="text-sm font-semibold text-muted">
              AI Receptionist
            </h2>
            <Badge tone={meta.tone} dot pulse={state === "live"}>
              {meta.label}
            </Badge>
          </div>

          {phone ? (
            <p className="mt-4 text-[1.875rem] leading-tight font-bold tracking-tight tabular-nums sm:text-[2.5rem]">
              {formatPhone(phone.e164)}
            </p>
          ) : (
            <p className="mt-4 text-2xl font-bold tracking-tight text-muted sm:text-3xl">
              Number not assigned yet
            </p>
          )}
          <p className="mt-2 max-w-xl text-[0.9375rem] leading-relaxed text-muted">{meta.description}</p>

          <div className="mt-6 flex flex-wrap gap-2.5">
            {state === "live" && phone ? (
              <ButtonLink href={`tel:${phone.e164}`} leadingIcon={<PhoneIcon size={16} />}>
                Test receptionist
              </ButtonLink>
            ) : (
              <ButtonLink href="/dashboard/receptionist#setup">View setup progress</ButtonLink>
            )}
            {showConfigurationLink && (
              <ButtonLink
                href="/dashboard/configuration"
                variant="secondary"
                leadingIcon={<SlidersIcon size={16} />}
              >
                Review configuration
              </ButtonLink>
            )}
          </div>
        </div>

        <StatusVisual state={state} provisioning={provisioning} />
      </div>
    </section>
  );
}

function StatusVisual({
  state,
  provisioning,
}: {
  state: ReceptionistState;
  provisioning: ProvisioningView;
}) {
  if (state === "live") {
    return (
      <div aria-hidden className="relative mx-auto hidden size-40 items-center justify-center sm:flex">
        <span className="absolute inset-4 rounded-full bg-success/15 [animation:ring-pulse_2.6s_ease-out_infinite]" />
        <span className="absolute inset-4 rounded-full bg-success/10 [animation:ring-pulse_2.6s_ease-out_1.3s_infinite]" />
        <span className="relative flex size-24 items-center justify-center rounded-full bg-success text-white shadow-lg">
          <span className="waveform h-9">
            {[14, 26, 36, 22, 30, 16].map((height, index) => (
              <span key={index} style={{ height }} />
            ))}
          </span>
        </span>
      </div>
    );
  }

  if (state === "setting_up") {
    const total = Math.max(provisioning.total_steps, 1);
    const fraction = provisioning.completed_steps / total;
    const circumference = 2 * Math.PI * 42;
    const running = provisioning.steps.find((step) => step.status === "running");
    const next = running ?? provisioning.steps.find((step) => step.status === "pending");
    return (
      <div className="mx-auto flex items-center gap-5 lg:flex-col lg:gap-3 lg:text-center">
        <div className="relative size-28 shrink-0" aria-hidden>
          <svg viewBox="0 0 100 100" className="size-full -rotate-90">
            <circle cx="50" cy="50" r="42" fill="none" stroke="var(--surface-3)" strokeWidth="8" />
            <circle
              cx="50"
              cy="50"
              r="42"
              fill="none"
              stroke="var(--accent)"
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={circumference * (1 - fraction)}
              className="transition-[stroke-dashoffset] duration-700"
            />
          </svg>
          <span className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-xl font-bold tabular-nums">
              {provisioning.completed_steps}/{provisioning.total_steps}
            </span>
          </span>
        </div>
        <div className="max-w-[12rem]">
          <p className="text-xs font-semibold text-muted uppercase">Now</p>
          <p className="mt-0.5 text-sm font-semibold">{next ? stepLabel(next.step_name) : "Finishing up"}</p>
        </div>
      </div>
    );
  }

  const Icon = state === "billing" ? AlertTriangleIcon : XCircleIcon;
  return (
    <div
      aria-hidden
      className={cx(
        "mx-auto hidden size-28 items-center justify-center rounded-full sm:flex",
        state === "billing" ? "bg-warn-soft text-warn" : state === "attention" ? "bg-danger-soft text-danger" : "bg-surface-2 text-muted",
      )}
    >
      <Icon size={44} strokeWidth={1.5} />
    </div>
  );
}
