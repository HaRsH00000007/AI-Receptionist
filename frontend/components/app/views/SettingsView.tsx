"use client";

import { useState, type ReactNode } from "react";

import { ThemeToggle } from "@/app/ThemeToggle";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { CopyButton } from "@/components/ui/CopyButton";
import { LogOutIcon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { BRAND } from "@/lib/brand";
import { formatDate, humanize } from "@/lib/format";
import { businessTypeLabel } from "@/lib/types";

export function SettingsView() {
  const { session, tenant, tenantId, switchTenant, signOut } = useTenantData();
  const [signingOut, setSigningOut] = useState(false);
  const membership = session.memberships.find((entry) => entry.tenant_id === tenantId);

  return (
    <div className="space-y-6 lg:space-y-8">
      <PageHeader title="Settings" description="Your profile, your organization and your preferences." />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Profile" />
          <dl className="divide-y divide-line text-sm">
            <Row label="Name">{session.full_name ?? "Not set"}</Row>
            <Row label="Email">{session.email}</Row>
            <Row label="Role">{membership ? humanize(membership.role) : "—"}</Row>
            <Row label="Sign-in method">Email link</Row>
          </dl>
        </Card>

        <Card>
          <CardHeader title="Organization" />
          <dl className="divide-y divide-line text-sm">
            <Row label="Business">{tenant.name}</Row>
            <Row label="Business type">{businessTypeLabel(tenant.business_type)}</Row>
            <Row label="Timezone">{tenant.timezone}</Row>
            <Row label="Contact email">{tenant.contact_email}</Row>
            <Row label="Customer since">{formatDate(tenant.created_at)}</Row>
            <Row label="Account ID">
              <span className="font-mono text-xs break-all">{tenant.id}</span>
            </Row>
          </dl>
          <div className="border-t border-line px-5 py-3">
            <CopyButton value={tenant.id} label="Copy account ID" />
          </div>
        </Card>

        {session.memberships.length > 1 && (
          <Card className="lg:col-span-2">
            <CardHeader title="Your organizations" description="Switch between the businesses you can access." />
            <ul className="divide-y divide-line">
              {session.memberships.map((entry) => (
                <li key={entry.tenant_id} className="flex items-center justify-between gap-4 px-5 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">{entry.name}</p>
                    <p className="text-xs text-muted">{humanize(entry.role)}</p>
                  </div>
                  {entry.tenant_id === tenantId ? (
                    <Badge tone="accent">Current</Badge>
                  ) : (
                    <Button
                      size="sm"
                      variant="secondary"
                      aria-label={`Switch to ${entry.name}`}
                      onClick={() => switchTenant(entry.tenant_id)}
                    >
                      Switch
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        )}

        <Card>
          <CardHeader title="Appearance" description="Light or dark, remembered in this browser." />
          <div className="flex items-center justify-between gap-4 px-5 py-4">
            <p className="text-sm text-ink-2">Theme</p>
            <ThemeToggle />
          </div>
        </Card>

        <Card>
          <CardHeader title="Session" />
          <div className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted">Sign out of {BRAND.name} on this device.</p>
            <Button
              variant="secondary"
              leadingIcon={<LogOutIcon size={16} />}
              loading={signingOut}
              onClick={async () => {
                setSigningOut(true);
                await signOut();
                setSigningOut(false);
              }}
            >
              Sign out
            </Button>
          </div>
        </Card>
      </div>
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
