"use client";

import type { ReactNode } from "react";

import {
  EscalationSummary,
  HoursTable,
  generatedByLabel,
  greetingStyleLabel,
} from "@/components/app/BusinessDetails";
import { ProvisioningTimeline } from "@/components/app/ProvisioningTimeline";
import { ReceptionistHero } from "@/components/app/ReceptionistHero";
import { SupportButton } from "@/components/app/SupportButton";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { CopyButton } from "@/components/ui/CopyButton";
import { ProgressBar } from "@/components/ui/Feedback";
import { ChevronDownIcon, ListIcon, MicIcon, PhoneIcon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { formatDate, formatDateTime, formatPhone, humanize } from "@/lib/format";
import { billingReason, friendlyError, receptionistState } from "@/lib/status";
import { businessTypeLabel, type ProvisioningView } from "@/lib/types";

export function ReceptionistView() {
  const { tenant, provisioning, phone, agent, profile } = useTenantData();
  const live = receptionistState(tenant.status, provisioning.status) === "live";

  return (
    <div className="space-y-6 lg:space-y-8">
      <PageHeader
        title="AI Receptionist"
        description="Its status, its number, and the details it works from."
        actions={
          <>
            <ButtonLink href="/dashboard/calls" variant="secondary" leadingIcon={<ListIcon size={16} />}>
              View calls
            </ButtonLink>
            {live && phone && (
              <ButtonLink href={`tel:${phone.e164}`} leadingIcon={<PhoneIcon size={16} />}>
                Test call
              </ButtonLink>
            )}
          </>
        }
      />

      <ReceptionistHero tenant={tenant} provisioning={provisioning} phone={phone} />

      <SetupSection provisioning={provisioning} />

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader title="Phone number" action={phone && <Badge tone="success" dot>Active</Badge>} />
          <div className="p-5">
            {phone ? (
              <>
                <p className="text-2xl font-bold tracking-tight tabular-nums">{formatPhone(phone.e164)}</p>
                <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
                  <Detail label="Area code">{phone.area_code ?? "—"}</Detail>
                  <Detail label="Active since">{formatDate(phone.purchased_at)}</Detail>
                </dl>
                <div className="mt-5 flex flex-wrap gap-2">
                  <CopyButton value={phone.e164} label="Copy number" />
                  <ButtonLink href="/dashboard/phone" variant="ghost" size="sm">
                    Forwarding setup
                  </ButtonLink>
                </div>
              </>
            ) : (
              <p className="text-sm text-muted">
                A number is assigned during setup
                {tenant.area_code ? `, local to area code ${tenant.area_code}` : ""}.
              </p>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Voice & greeting"
            action={
              profile && (
                <Badge tone={profile.custom_greeting ? "accent" : "neutral"}>
                  {profile.custom_greeting ? "Your own wording" : "Written for you"}
                </Badge>
              )
            }
          />
          <div className="space-y-4 p-5">
            <div className="flex items-center gap-3">
              <span className="choice-icon">
                <MicIcon size={18} />
              </span>
              <div>
                <p className="text-sm font-semibold">{greetingStyleLabel(profile?.greeting_style)} style</p>
                <p className="text-sm text-muted">The voice is matched to your greeting style.</p>
              </div>
            </div>
            {profile?.greeting ? (
              <blockquote className="rounded-xl bg-surface-2 px-4 py-3 text-[0.9375rem] leading-relaxed text-ink">
                “{profile.greeting}”
              </blockquote>
            ) : profile?.custom_greeting ? (
              <blockquote className="rounded-xl bg-surface-2 px-4 py-3 text-[0.9375rem] leading-relaxed text-ink">
                “{profile.custom_greeting}”
              </blockquote>
            ) : (
              <p className="text-sm text-muted">Your greeting is written from your details during setup.</p>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Hours" description={profile?.hours ? `Times in ${profile.hours.timezone}` : undefined} />
          <div className="p-5">
            {profile?.hours ? (
              <HoursTable hours={profile.hours} />
            ) : (
              <p className="text-sm text-ink-2">{profile?.hours_raw ?? "No hours were provided."}</p>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Escalation" />
          <div className="p-5">
            {profile ? (
              <EscalationSummary profile={profile} />
            ) : (
              <p className="text-sm text-muted">Escalation details couldn&apos;t be loaded.</p>
            )}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="Configuration" description="The version of instructions your receptionist is using." />
        <dl className="grid gap-x-6 gap-y-5 p-5 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <Detail label="Version">
            {agent?.config_version ?? profile?.config_version
              ? `Version ${agent?.config_version ?? profile?.config_version}`
              : "Not live yet"}
          </Detail>
          <Detail label="Written by">{generatedByLabel(agent?.generated_by)}</Detail>
          <Detail label="Last synced">{formatDateTime(agent?.synced_at)}</Detail>
          <Detail label="Receptionist">{agent ? humanize(agent.status) : "Not created yet"}</Detail>
          <Detail label="Business type">{businessTypeLabel(tenant.business_type)}</Detail>
          <Detail label="Timezone">{tenant.timezone}</Detail>
        </dl>
      </Card>

      <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-[0.9375rem] font-semibold">Need to change something, or pause answering?</p>
          <p className="mt-1 text-sm text-muted">
            Editing and pausing your receptionist aren&apos;t available in the dashboard yet. Our team
            can make the change for you.
          </p>
        </div>
        <SupportButton />
      </Card>
    </div>
  );
}

function SetupSection({ provisioning }: { provisioning: ProvisioningView }) {
  const done = provisioning.status === "active";
  const failed = provisioning.status === "failed" || provisioning.status === "compensated";
  const billing = provisioning.status === "billing_blocked";
  const friendly = friendlyError(provisioning.last_error);

  const body = (
    <>
      {billing && (
        <Alert tone="warn" title="Waiting for your subscription" className="mb-6">
          {billingReason(provisioning.billing?.reason)} Setup continues automatically once payment is
          confirmed.
        </Alert>
      )}
      {failed && (
        <Alert tone="danger" title={friendly.title} className="mb-6">
          {friendly.explanation} Our team can see exactly where setup stopped. Reference{" "}
          <span className="font-mono">{provisioning.correlation_id}</span>.
        </Alert>
      )}
      <ProvisioningTimeline provisioning={provisioning} />
    </>
  );

  if (done) {
    return (
      <details id="setup" className="card group overflow-hidden">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 transition-colors hover:bg-surface-2 [&::-webkit-details-marker]:hidden">
          <span className="min-w-0">
            <span className="block text-[0.9375rem] font-semibold">Setup history</span>
            <span className="block text-sm text-muted">
              All {provisioning.total_steps} steps completed
              {provisioning.finished_at ? ` · ${formatDateTime(provisioning.finished_at)}` : ""}
            </span>
          </span>
          <ChevronDownIcon className="shrink-0 text-muted transition-transform group-open:rotate-180" />
        </summary>
        <div className="border-t border-line px-5 py-6">{body}</div>
      </details>
    );
  }

  return (
    <Card id="setup">
      <CardHeader
        title="Setup progress"
        description={`${provisioning.completed_steps} of ${provisioning.total_steps} steps complete`}
        action={
          failed ? (
            <Badge tone="danger">Stopped</Badge>
          ) : billing ? (
            <Badge tone="warn">Paused</Badge>
          ) : (
            <Badge tone="accent" dot pulse>
              Updating live
            </Badge>
          )
        }
      />
      <div className="px-5 py-6">
        <ProgressBar
          value={provisioning.completed_steps}
          max={provisioning.total_steps}
          label="Setup progress"
          tone={failed ? "danger" : billing ? "warn" : "accent"}
          className="mb-6"
        />
        {body}
      </div>
    </Card>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 font-medium break-words text-ink">{children}</dd>
    </div>
  );
}
