"use client";

import { StatCard } from "@/components/app/StatCard";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { UsageMeter, UsageNotice } from "@/components/app/Usage";
import { ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ClockIcon, PhoneIncomingIcon, TagIcon, WaveformIcon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { formatCalendarDate, formatNumber } from "@/lib/format";
import { planName } from "@/lib/plans";

export function UsageView() {
  const { usage } = useTenantData();
  const remaining = Math.max(0, usage.included_minutes - usage.call_minutes);
  const average = usage.call_count > 0 ? usage.call_minutes / usage.call_count : null;

  return (
    <div className="space-y-6 lg:space-y-8">
      <PageHeader
        title="Usage"
        description="Calls and minutes for your current billing period, measured against your plan."
      />

      <UsageNotice usage={usage} />

      <Card>
        <CardHeader
          title="This billing period"
          description={`${formatCalendarDate(usage.period_start)} – ${formatCalendarDate(usage.period_end)}`}
        />
        <div className="p-5 sm:p-6">
          <UsageMeter usage={usage} />
        </div>
      </Card>

      <section aria-label="Usage figures" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Calls" value={formatNumber(usage.call_count)} hint="This period" icon={<PhoneIncomingIcon size={16} />} />
        <StatCard label="Minutes used" value={formatNumber(usage.call_minutes)} hint="This period" icon={<ClockIcon size={16} />} />
        <StatCard
          label="Minutes remaining"
          value={formatNumber(remaining)}
          hint={`of ${formatNumber(usage.included_minutes)} included`}
          icon={<WaveformIcon size={16} />}
        />
        <StatCard
          label="Average call"
          value={average === null ? "—" : `${average < 10 ? average.toFixed(1) : Math.round(average)} min`}
          hint="Minutes per call this period"
          icon={<TagIcon size={16} />}
        />
      </section>

      <Card>
        <CardHeader
          title="Plan limits"
          description={`You're on ${planName(usage.plan)}.`}
          action={
            <ButtonLink href="/dashboard/billing" variant="ghost" size="sm">
              View plan
            </ButtonLink>
          }
        />
        <dl className="grid gap-x-6 gap-y-5 p-5 text-sm sm:grid-cols-2">
          <Limit label="Included minutes" value={`${formatNumber(usage.included_minutes)} per billing period`} />
          <Limit
            label="At the limit"
            value={
              usage.blocks_on_overage
                ? "Calls stop being answered until the next period"
                : "Calls keep being answered; extra minutes are billed at your plan rate"
            }
          />
        </dl>
      </Card>
    </div>
  );
}

function Limit({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 font-semibold text-ink">{value}</dd>
    </div>
  );
}
