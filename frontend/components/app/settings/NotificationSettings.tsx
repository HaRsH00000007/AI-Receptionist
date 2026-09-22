"use client";

/**
 * Where the receptionist's emails go, and when.
 *
 * Describes what the product does today rather than offering switches it has no
 * API for: every summarized call is emailed to the business's contact address.
 * Changing that address moves where account email goes too, so it goes through
 * support rather than a field here.
 */

import { SupportButton } from "@/components/app/SupportButton";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { Card, CardHeader } from "@/components/ui/Card";
import { planById } from "@/lib/plans";

import { Row } from "./BusinessSettings";

export function NotificationSettings() {
  const { tenant } = useTenantData();
  const plan = planById(tenant.plan);
  const summaries = plan ? plan.features.includes("call_summaries") : true;

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Notification email" />
        <div className="space-y-2 p-5">
          <p className="text-sm text-muted">Call summaries and account emails are sent to:</p>
          <p className="text-[0.9375rem] font-semibold break-all">{tenant.contact_email}</p>
        </div>
        <div className="flex justify-end border-t border-line px-5 py-3">
          <SupportButton size="sm" label="Change this address" />
        </div>
      </Card>

      <Card>
        <CardHeader title="What we email you" />
        <dl className="divide-y divide-line text-sm">
          <Row label="Call summaries">{summaries ? "After every call" : "Not on your plan"}</Row>
          <Row label="Urgent calls">Marked urgent in the subject</Row>
          <Row label="Setup">When your receptionist goes live, or if setup stops</Row>
          {/* Shown in the dashboard only: no usage email is sent yet. */}
          <Row label="Usage">Shown on your dashboard at 80% of your minutes</Row>
        </dl>
      </Card>
    </div>
  );
}
