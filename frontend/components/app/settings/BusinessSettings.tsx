"use client";

/**
 * The business: its identity, and what the receptionist knows about it.
 *
 * Name and type are shown, not edited here. The type chooses the vertical —
 * the prompt template, the shared agent that answers, the integrations offered
 * — so changing it is a migration our team runs, not a form field. Services
 * and hours are edited on the Agent page, where saving publishes a new
 * configuration version.
 */

import type { ReactNode } from "react";

import { HoursTable, ServicesList } from "@/components/app/BusinessDetails";
import { SupportButton } from "@/components/app/SupportButton";
import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { CopyButton } from "@/components/ui/CopyButton";
import { formatDate } from "@/lib/format";
import { businessTypeLabel } from "@/lib/types";

export function BusinessSettings() {
  const { tenant, profile } = useTenantData();
  const canManage = useCanManage();

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Business" />
        <dl className="divide-y divide-line text-sm">
          <Row label="Business name">{tenant.name}</Row>
          <Row label="Business type">{businessTypeLabel(tenant.business_type)}</Row>
          <Row label="Timezone">{tenant.timezone}</Row>
          <Row label="Customer since">{formatDate(tenant.created_at)}</Row>
          <Row label="Account ID">
            <span className="font-mono text-xs break-all">{tenant.id}</span>
          </Row>
        </dl>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-5 py-3">
          <CopyButton value={tenant.id} label="Copy account ID" />
          <p className="text-xs text-muted">To change your business name or type, contact us.</p>
        </div>
      </Card>

      <Card>
        <CardHeader title="Contact details" />
        <dl className="divide-y divide-line text-sm">
          <Row label="Contact email">{tenant.contact_email}</Row>
          <Row label="Area code">{tenant.area_code ?? "—"}</Row>
        </dl>
        <div className="flex justify-end border-t border-line px-5 py-3">
          <SupportButton size="sm" label="Update contact details" />
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Services"
          action={
            canManage && (
              <ButtonLink href="/dashboard/agent" size="sm" variant="ghost">
                Edit
              </ButtonLink>
            )
          }
        />
        <div className="p-5">
          <ServicesList services={profile?.services ?? []} />
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Operating hours"
          description={profile?.hours ? `Times in ${profile.hours.timezone}` : undefined}
          action={
            canManage && (
              <ButtonLink href="/dashboard/agent" size="sm" variant="ghost">
                Edit
              </ButtonLink>
            )
          }
        />
        <div className="p-5">
          {profile?.hours ? (
            <HoursTable hours={profile.hours} />
          ) : (
            <p className="text-sm text-ink-2">{profile?.hours_raw ?? "No hours were provided."}</p>
          )}
        </div>
      </Card>
    </div>
  );
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 px-5 py-3">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right font-medium break-words text-ink">{children}</dd>
    </div>
  );
}
