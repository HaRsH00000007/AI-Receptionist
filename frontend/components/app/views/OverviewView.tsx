"use client";

import { useState, type ReactNode } from "react";

import { greetingStyleLabel } from "@/components/app/BusinessDetails";
import { CallDetailDrawer } from "@/components/app/CallDetailDrawer";
import { CallList } from "@/components/app/CallList";
import { ReceptionistHero } from "@/components/app/ReceptionistHero";
import { StatCard } from "@/components/app/StatCard";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { UsageMeter, UsageNotice } from "@/components/app/Usage";
import { useNow } from "@/components/app/useNow";
import { ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  ClockIcon,
  ListIcon,
  PhoneIcon,
  PhoneIncomingIcon,
  UserIcon,
} from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { formatNumber, formatPhone, pluralize } from "@/lib/format";
import { planName } from "@/lib/plans";
import { isUrgent, receptionistState } from "@/lib/status";
import { businessTypeLabel, type CallView } from "@/lib/types";

const RECENT_CALLS = 6;

export function OverviewView() {
  const { tenant, provisioning, phone, agent, usage, calls, profile, session } = useTenantData();
  const now = useNow();
  const [selected, setSelected] = useState<CallView | null>(null);

  const live = receptionistState(tenant.status, provisioning.status) === "live";
  const callbacks = calls.filter((call) => call.callback_number).length;
  const urgent = calls.filter((call) => isUrgent(call.urgency)).length;
  const recentWindow = calls.length ? `In your last ${pluralize(calls.length, "call")}` : "Counted once calls come in";
  const firstName = session.impersonated ? null : session.full_name?.trim().split(/\s+/)[0];

  return (
    <div className="space-y-6 lg:space-y-8">
      <PageHeader
        title={firstName ? `Welcome back, ${firstName}` : "Overview"}
        description={`Here's how the receptionist for ${tenant.name} is doing.`}
      />

      <UsageNotice usage={usage} />

      <ReceptionistHero tenant={tenant} provisioning={provisioning} phone={phone} />

      <section aria-label="Key numbers" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Calls handled"
          value={formatNumber(usage.call_count)}
          hint="This billing period"
          icon={<PhoneIncomingIcon size={16} />}
          href="/dashboard/calls"
        />
        <StatCard
          label="Minutes used"
          value={formatNumber(usage.call_minutes)}
          hint={`of ${formatNumber(usage.included_minutes)} included`}
          icon={<ClockIcon size={16} />}
          href="/dashboard/usage"
        />
        <StatCard
          label="Callbacks captured"
          value={formatNumber(callbacks)}
          hint={recentWindow}
          icon={<UserIcon size={16} />}
          href="/dashboard/calls"
        />
        <StatCard
          label="Urgent calls"
          value={formatNumber(urgent)}
          hint={recentWindow}
          icon={<AlertTriangleIcon size={16} />}
          href="/dashboard/calls?urgent=1"
        />
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem] xl:grid-cols-[minmax(0,1fr)_22rem]">
        <Card className="min-w-0 self-start overflow-hidden">
          <CardHeader
            title="Recent calls"
            description="Summaries appear moments after each call ends."
            action={
              calls.length > 0 && (
                <ButtonLink
                  href="/dashboard/calls"
                  variant="ghost"
                  size="sm"
                  trailingIcon={<ArrowRightIcon size={15} />}
                >
                  View all
                </ButtonLink>
              )
            }
          />
          {calls.length === 0 ? (
            <div className="p-5">
              <EmptyState
                icon={<ListIcon />}
                title="No calls yet"
                description={
                  live && phone
                    ? `Call ${formatPhone(phone.e164)} to hear your receptionist answer. The call will show up here with a summary.`
                    : "Calls will appear here once your receptionist is live."
                }
                action={
                  live && phone ? (
                    <ButtonLink href={`tel:${phone.e164}`} leadingIcon={<PhoneIcon size={16} />}>
                      Test receptionist
                    </ButtonLink>
                  ) : undefined
                }
              />
            </div>
          ) : (
            <CallList calls={calls.slice(0, RECENT_CALLS)} onSelect={setSelected} now={now} compact />
          )}
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader
              title="Usage"
              description={planName(usage.plan)}
              action={
                <ButtonLink href="/dashboard/usage" variant="ghost" size="sm">
                  Details
                </ButtonLink>
              }
            />
            <div className="p-5">
              <UsageMeter usage={usage} />
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Your receptionist"
              action={
                <ButtonLink href="/dashboard/configuration" variant="ghost" size="sm">
                  Configuration
                </ButtonLink>
              }
            />
            <dl className="divide-y divide-line text-sm">
              <Row label="Business type">{businessTypeLabel(tenant.business_type)}</Row>
              <Row label="Greeting style">{greetingStyleLabel(profile?.greeting_style)}</Row>
              <Row label="Hours">{profile?.hours_raw ?? "—"}</Row>
              <Row label="Configuration">
                {agent?.config_version ? `Version ${agent.config_version}` : "Not live yet"}
              </Row>
            </dl>
          </Card>
        </div>
      </div>

      <CallDetailDrawer call={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 px-5 py-3">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right font-medium break-words text-ink">{children}</dd>
    </div>
  );
}
