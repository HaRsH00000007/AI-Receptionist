"use client";

/**
 * Home: the receptionist's number, how it is doing, and the latest calls.
 *
 * Every figure here is measured. Where the product does not measure something
 * yet — hang-ups, spam, transfers — the card says "Not tracked yet" rather than
 * showing a zero that reads as a result. Texts and booked events are tied to
 * the features that would produce them, so those cards point at SMS and
 * Integrations instead of inventing a count.
 */

import { useState, type ReactNode } from "react";

import { greetingStyleLabel } from "@/components/app/BusinessDetails";
import { CallDetailDrawer } from "@/components/app/CallDetailDrawer";
import { CallList } from "@/components/app/CallList";
import { StatCard } from "@/components/app/StatCard";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { UsageMeter, UsageNotice } from "@/components/app/Usage";
import { useLoad } from "@/components/app/useLoad";
import { useNow } from "@/components/app/useNow";
import { Badge } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { CopyButton } from "@/components/ui/CopyButton";
import { EmptyState } from "@/components/ui/Feedback";
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  CalendarIcon,
  ClockIcon,
  HandoffIcon,
  ListIcon,
  PhoneForwardIcon,
  PhoneIcon,
  PhoneIncomingIcon,
  PhoneOffIcon,
  SendIcon,
  UsersIcon,
} from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { getDashboardSummary, getSmsState, listIntegrations } from "@/lib/api";
import { formatDuration, formatNumber, formatPhone } from "@/lib/format";
import { planName } from "@/lib/plans";
import { RECEPTIONIST_STATE, receptionistState } from "@/lib/status";
import { businessTypeLabel, type CallView } from "@/lib/types";

const RECENT_CALLS = 6;
const WINDOW_DAYS = 30;
const NOT_TRACKED = "Not tracked yet";

export function HomeView() {
  const { tenant, provisioning, phone, agent, usage, calls, profile, session, tenantId } =
    useTenantData();
  const now = useNow();
  const [selected, setSelected] = useState<CallView | null>(null);

  const summary = useLoad(() => getDashboardSummary(tenantId, WINDOW_DAYS), `summary:${tenantId}`);
  // Read to label two cards honestly. Either failing only costs its card.
  const integrations = useLoad(() => listIntegrations(tenantId), `integrations:${tenantId}`);
  const sms = useLoad(() => getSmsState(tenantId), `sms:${tenantId}`);

  const state = receptionistState(tenant.status, provisioning.status);
  const meta = RECEPTIONIST_STATE[state];
  const firstName = session.impersonated ? null : session.full_name?.trim().split(/\s+/)[0];
  const stats = summary.data;
  const pending = summary.loading && !stats;
  const windowHint = `Last ${WINDOW_DAYS} days`;

  const calendar = integrations.data?.integrations.find(
    (entry) => entry.category === "calendar" && entry.status === "connected",
  );

  return (
    <div className="space-y-6 lg:space-y-8">
      <PageHeader
        title={firstName ? `Welcome back, ${firstName}` : "Home"}
        description={`Here's how the receptionist for ${tenant.name} is doing.`}
      />

      <UsageNotice usage={usage} />

      <section
        aria-labelledby="agent-line-heading"
        className="relative overflow-hidden rounded-2xl border border-line bg-surface shadow-sm"
      >
        <div aria-hidden className="grid-backdrop pointer-events-none absolute inset-0 opacity-60" />
        <div className="relative flex flex-col gap-6 p-6 sm:p-8 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <h2 id="agent-line-heading" className="text-sm font-semibold text-muted">
                Call your agent at
              </h2>
              <Badge tone={meta.tone} dot pulse={state === "live"}>
                {state === "live" ? "Answering calls" : meta.label}
              </Badge>
            </div>
            <p className="mt-3 text-[1.875rem] leading-tight font-bold tracking-tight tabular-nums sm:text-[2.5rem]">
              {phone ? formatPhone(phone.e164) : "Number not assigned yet"}
            </p>
            <p className="mt-2 max-w-xl text-[0.9375rem] leading-relaxed text-muted">
              Call it yourself to hear exactly what your callers hear. To keep your existing
              business number, forward it here.
            </p>
          </div>
          {phone && (
            <div className="flex flex-wrap gap-2.5 lg:flex-col lg:items-stretch">
              <ButtonLink href={`tel:${phone.e164}`} leadingIcon={<PhoneIcon size={16} />}>
                Call your agent
              </ButtonLink>
              <CopyButton value={phone.e164} label="Copy number" size="md" />
              <ButtonLink
                href="/dashboard/settings/phone"
                variant="ghost"
                leadingIcon={<PhoneForwardIcon size={16} />}
              >
                Forward your number
              </ButtonLink>
            </div>
          )}
        </div>
      </section>

      <section aria-label="Calls this month" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Answered calls"
          value={pending ? "…" : stats ? formatNumber(stats.answered_calls) : "—"}
          hint={windowHint}
          icon={<PhoneIncomingIcon size={16} />}
          href="/dashboard/calls"
        />
        <StatCard
          label="Hang-ups & missed"
          value="—"
          hint={NOT_TRACKED}
          icon={<PhoneOffIcon size={16} />}
        />
        <StatCard
          label="Spam & blocked"
          value="—"
          hint={NOT_TRACKED}
          icon={<AlertTriangleIcon size={16} />}
        />
        <StatCard
          label="Average call"
          value={
            pending
              ? "…"
              : stats?.average_duration_s != null
                ? formatDuration(Math.round(stats.average_duration_s))
                : "—"
          }
          hint={stats?.average_duration_s != null ? windowHint : "Shown once calls come in"}
          icon={<ClockIcon size={16} />}
        />
      </section>

      <section aria-label="More activity" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Transferred" value="—" hint={NOT_TRACKED} icon={<HandoffIcon size={16} />} />
        <StatCard
          label="Texts sent"
          value={sms.data?.can_send ? "0" : "—"}
          hint={
            sms.data?.can_send
              ? "SMS is enabled"
              : sms.data
                ? "SMS isn't enabled yet"
                : "SMS status unavailable"
          }
          icon={<SendIcon size={16} />}
          href="/dashboard/sms"
        />
        <StatCard
          label="Scheduled events"
          value="—"
          hint={calendar ? `${calendar.name} connected · booking not tracked yet` : "Connect a calendar"}
          icon={<CalendarIcon size={16} />}
          href="/dashboard/integrations"
        />
        <StatCard
          label="Contacts"
          value={pending ? "…" : stats ? formatNumber(stats.total_contacts) : "—"}
          hint="Everyone who has called"
          icon={<UsersIcon size={16} />}
          href="/dashboard/contacts"
        />
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem] xl:grid-cols-[minmax(0,1fr)_22rem]">
        <Card className="min-w-0 self-start overflow-hidden">
          <CardHeader
            title="Latest calls"
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
                  phone
                    ? `Call ${formatPhone(phone.e164)} to hear your receptionist answer. The call will show up here with a summary.`
                    : "Calls will appear here once your receptionist is live."
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
                <ButtonLink href="/dashboard/settings/billing" variant="ghost" size="sm">
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
              title="Your agent"
              action={
                <ButtonLink href="/dashboard/agent" variant="ghost" size="sm">
                  Manage
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
