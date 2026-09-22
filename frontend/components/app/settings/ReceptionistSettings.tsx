"use client";

/**
 * The receptionist, summarized for Settings. Editing happens on the Agent page,
 * where a save publishes a new configuration version; this section links there
 * rather than keeping a second editor that could drift from the first.
 */

import { EscalationSummary, generatedByLabel, greetingStyleLabel } from "@/components/app/BusinessDetails";
import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { SlidersIcon } from "@/components/ui/icons";
import { formatDateTime, humanize } from "@/lib/format";

import { Row } from "./BusinessSettings";

export function ReceptionistSettings() {
  const { profile, agent } = useTenantData();
  const canManage = useCanManage();
  const editLink = canManage && (
    <ButtonLink href="/dashboard/agent" size="sm" variant="ghost" leadingIcon={<SlidersIcon size={15} />}>
      Edit on the Agent page
    </ButtonLink>
  );

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Greeting" action={editLink} />
        <div className="space-y-3 p-5">
          <p className="text-sm text-muted">
            {greetingStyleLabel(profile?.greeting_style)} style ·{" "}
            {profile?.custom_greeting ? "your own wording" : "written for you"}
          </p>
          {(profile?.greeting ?? profile?.custom_greeting) ? (
            <blockquote className="rounded-xl bg-surface-2 px-4 py-3 text-[0.9375rem] leading-relaxed text-ink">
              “{profile?.greeting ?? profile?.custom_greeting}”
            </blockquote>
          ) : (
            <p className="text-sm text-muted">Your greeting is written during setup.</p>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="Escalation rules" action={editLink} />
        <div className="p-5">
          {profile ? <EscalationSummary profile={profile} /> : <p className="text-sm text-muted">—</p>}
        </div>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader title="Agent configuration" />
        <dl className="divide-y divide-line text-sm">
          <Row label="Live version">
            {agent?.config_version ? `Version ${agent.config_version}` : "Not live yet"}
          </Row>
          <Row label="Written by">{generatedByLabel(agent?.generated_by)}</Row>
          <Row label="Voice agent">{agent ? humanize(agent.status) : "Not created yet"}</Row>
          <Row label="Last synced">{formatDateTime(agent?.synced_at)}</Row>
        </dl>
      </Card>
    </div>
  );
}
