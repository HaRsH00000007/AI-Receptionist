"use client";

import type { ReactNode } from "react";

import { ForwardingGuide } from "@/components/app/ForwardingGuide";
import { SupportButton } from "@/components/app/SupportButton";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { Badge } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { CopyButton } from "@/components/ui/CopyButton";
import { EmptyState } from "@/components/ui/Feedback";
import { HashIcon, PhoneIcon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { formatDate, formatPhone, humanize } from "@/lib/format";
import { receptionistState } from "@/lib/status";

/** `embedded` drops the page header, for use as a section of Settings. */
export function PhoneView({ embedded = false }: { embedded?: boolean } = {}) {
  const { tenant, provisioning, phone } = useTenantData();
  const state = receptionistState(tenant.status, provisioning.status);

  return (
    <div className="space-y-6 lg:space-y-8">
      {!embedded && (
        <PageHeader
          title="Phone Numbers"
          description="The number your receptionist answers, and how to send your existing line to it."
        />
      )}

      {phone ? (
        <Card className="overflow-hidden">
          <div className="grid gap-6 p-6 sm:p-8 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-muted">AI receptionist line</p>
                <Badge tone={phone.status === "active" ? "success" : "neutral"} dot>
                  {humanize(phone.status)}
                </Badge>
              </div>
              <p className="mt-3 text-[2rem] leading-tight font-bold tracking-tight tabular-nums sm:text-[2.5rem]">
                {formatPhone(phone.e164)}
              </p>
              <dl className="mt-5 flex flex-wrap gap-x-10 gap-y-4 text-sm">
                <Detail label="Area code">{phone.area_code ?? "—"}</Detail>
                <Detail label="Active since">{formatDate(phone.purchased_at)}</Detail>
                <Detail label="Answered by">AI receptionist</Detail>
              </dl>
            </div>
            <div className="flex flex-wrap gap-2.5 md:flex-col">
              {state === "live" && (
                <ButtonLink href={`tel:${phone.e164}`} leadingIcon={<PhoneIcon size={16} />}>
                  Test call
                </ButtonLink>
              )}
              <CopyButton value={phone.e164} label="Copy number" size="md" />
            </div>
          </div>
        </Card>
      ) : (
        <EmptyState
          icon={<HashIcon />}
          title={state === "attention" ? "No number was assigned" : "Your number is on its way"}
          description={
            state === "attention"
              ? "Setup stopped before a number was assigned. Our team can see what happened."
              : `A local number${tenant.area_code ? ` in area code ${tenant.area_code}` : ""} is assigned during setup.`
          }
          action={
            <ButtonLink href="/dashboard/setup" variant="secondary">
              View setup progress
            </ButtonLink>
          }
          className="bg-surface"
        />
      )}

      <ForwardingGuide number={phone?.e164 ?? null} />

      <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-[0.9375rem] font-semibold">Need another number or a different area code?</p>
          <p className="mt-1 text-sm text-muted">
            Your receptionist answers on one number. Adding or changing numbers isn&apos;t available in
            the dashboard yet, so our team handles it for you.
          </p>
        </div>
        <SupportButton />
      </Card>
    </div>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 font-semibold text-ink">{children}</dd>
    </div>
  );
}
