"use client";

/**
 * The receptionist: its status, what it knows, and the controls to change it.
 *
 * Saving does not edit the running configuration. It publishes a new version —
 * the previous one is kept, unchanged, and every past call still points at the
 * version that answered it — and then updates the voice agent. The page reports
 * those two outcomes separately, because they can differ: a change can be
 * saved while the voice vendor is briefly unreachable.
 *
 * Owners and admins can edit. Members see the same information read-only; the
 * API refuses their writes regardless.
 */

import { useState, type FormEvent, type ReactNode } from "react";

import {
  ConversationPreview,
  EscalationSummary,
  GREETING_STYLE_COPY,
  HoursTable,
  ServicesList,
  buildConversationPreview,
  generatedByLabel,
  greetingStyleLabel,
} from "@/components/app/BusinessDetails";
import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { useLoad } from "@/components/app/useLoad";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Feedback";
import { ChoiceGroup, TextAreaField, TextField } from "@/components/ui/Field";
import { PhoneIcon, SlidersIcon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, listConfigVersions, updateAgentSettings } from "@/lib/api";
import { formatDateTime, formatPhone, humanize } from "@/lib/format";
import { GREETING_MAX_LENGTH } from "@/lib/greetings";
import { RECEPTIONIST_STATE, receptionistState } from "@/lib/status";
import {
  GREETING_STYLES,
  businessTypeLabel,
  type AgentSettingsUpdate,
  type BusinessProfileView,
  type GreetingStyle,
} from "@/lib/types";

type Errors = Partial<Record<keyof AgentSettingsUpdate, string>>;

function initialForm(profile: BusinessProfileView | null): AgentSettingsUpdate {
  return {
    services: (profile?.services ?? []).join(", "),
    operating_hours: profile?.hours_raw ?? "",
    greeting_style: (profile?.greeting_style ?? "professional") as GreetingStyle,
    custom_greeting: profile?.custom_greeting ?? "",
    escalation_rules: profile?.escalation_raw ?? "",
  };
}

function validate(form: AgentSettingsUpdate): Errors {
  const errors: Errors = {};
  if (!form.services.split(/[,;\n]/).some((item) => item.trim())) {
    errors.services = "List at least one service.";
  }
  if (!form.operating_hours.trim()) errors.operating_hours = "Tell callers when you're open.";
  if (form.custom_greeting.trim().length > GREETING_MAX_LENGTH) {
    errors.custom_greeting = `Keep the opening line under ${GREETING_MAX_LENGTH} characters.`;
  }
  if (form.escalation_rules.length > 2000) errors.escalation_rules = "Keep this under 2,000 characters.";
  return errors;
}

export function AgentView() {
  const { tenant, provisioning, phone, agent, profile, tenantId, refresh } = useTenantData();
  const canManage = useCanManage();
  const toast = useToast();
  const versions = useLoad(() => listConfigVersions(tenantId), `versions:${tenantId}`);

  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<AgentSettingsUpdate>(() => initialForm(profile));
  const [errors, setErrors] = useState<Errors>({});
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "warn"; text: string } | null>(null);

  const state = receptionistState(tenant.status, provisioning.status);
  const meta = RECEPTIONIST_STATE[state];
  const live = state === "live";
  const version = agent?.config_version ?? profile?.config_version ?? null;
  const preview = buildConversationPreview(tenant.name, profile);

  function update<K extends keyof AgentSettingsUpdate>(key: K, value: AgentSettingsUpdate[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
    setErrors((previous) => ({ ...previous, [key]: undefined }));
  }

  function startEditing() {
    setForm(initialForm(profile));
    setErrors({});
    setResult(null);
    setEditing(true);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const found = validate(form);
    setErrors(found);
    if (Object.values(found).some(Boolean)) return;

    setSaving(true);
    try {
      const outcome = await updateAgentSettings(tenantId, {
        ...form,
        custom_greeting: form.custom_greeting.trim(),
      });
      setResult({ tone: outcome.synced ? "success" : "warn", text: outcome.detail });
      toast({
        tone: outcome.synced ? "success" : "info",
        title: outcome.previous_version
          ? `Version ${outcome.config_version} is live`
          : "No changes to publish",
        description: outcome.detail,
      });
      setEditing(false);
      refresh();
      versions.reload();
    } catch (caught) {
      const failure = caught instanceof ApiError ? caught : null;
      if (failure && Object.keys(failure.fieldErrors).length) {
        setErrors(failure.fieldErrors as Errors);
      }
      toast({
        tone: "danger",
        title: "Couldn't save your changes",
        description: failure?.message ?? "Please try again.",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6 lg:space-y-8">
      <PageHeader
        title="Agent"
        description="Your AI receptionist: what it knows about your business, and how it answers."
        actions={
          live && phone ? (
            <ButtonLink href={`tel:${phone.e164}`} leadingIcon={<PhoneIcon size={16} />}>
              Call your agent
            </ButtonLink>
          ) : undefined
        }
      />

      <Card className="overflow-hidden">
        <dl className="grid gap-px bg-line sm:grid-cols-2 lg:grid-cols-3">
          <Fact label="Status">
            <Badge tone={meta.tone} dot pulse={live}>
              {live ? "Live" : meta.label}
            </Badge>
          </Fact>
          <Fact label="Phone number">
            <span className="tabular-nums">{phone ? formatPhone(phone.e164) : "Not assigned yet"}</span>
          </Fact>
          <Fact label="Business">{tenant.name}</Fact>
          <Fact label="Business type">{businessTypeLabel(tenant.business_type)}</Fact>
          <Fact label="Configuration">
            {version ? `Version ${version}` : "Not live yet"}
            <span className="mt-0.5 block text-xs font-normal text-muted">
              {generatedByLabel(agent?.generated_by)}
            </span>
          </Fact>
          <Fact label="Voice agent synced">{formatDateTime(agent?.synced_at)}</Fact>
        </dl>
      </Card>

      {result && !editing && (
        <Alert tone={result.tone} title={result.tone === "success" ? "Saved" : "Saved, not synced yet"}>
          {result.text}
        </Alert>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_21rem] xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0 space-y-6">
          {editing ? (
            <Card>
              <CardHeader
                title="Edit your receptionist"
                description="Saving publishes a new version. The current one is kept in your history."
              />
              <form className="space-y-6 p-5 sm:p-6" noValidate onSubmit={save}>
                <TextAreaField
                  label="Services"
                  rows={3}
                  value={form.services}
                  onChange={(event) => update("services", event.target.value)}
                  hint="Separate services with commas."
                  error={errors.services}
                />
                <TextField
                  label="Operating hours"
                  value={form.operating_hours}
                  onChange={(event) => update("operating_hours", event.target.value)}
                  placeholder="Mon-Fri 9-5, Sat 10-2"
                  error={errors.operating_hours}
                />
                <ChoiceGroup<GreetingStyle>
                  legend="Greeting style"
                  name="greeting_style"
                  value={form.greeting_style}
                  onChange={(value) => update("greeting_style", value)}
                  options={GREETING_STYLES.map((option) => ({
                    value: option.value,
                    label: option.label,
                    description: GREETING_STYLE_COPY[option.value].description,
                  }))}
                  columns={3}
                />
                <TextAreaField
                  label="Opening line"
                  optional
                  rows={2}
                  maxLength={GREETING_MAX_LENGTH}
                  value={form.custom_greeting}
                  onChange={(event) => update("custom_greeting", event.target.value)}
                  placeholder={GREETING_STYLE_COPY[form.greeting_style].sample(tenant.name)}
                  hint="Callers hear this word for word. Leave it blank and we'll write one from your details."
                  error={errors.custom_greeting}
                />
                <TextAreaField
                  label="Escalation rules"
                  optional
                  rows={3}
                  value={form.escalation_rules}
                  onChange={(event) => update("escalation_rules", event.target.value)}
                  placeholder="Notify me right away if a caller has an emergency."
                  hint="When should the receptionist interrupt you, rather than take a message?"
                  error={errors.escalation_rules}
                />
                <div className="flex flex-wrap justify-end gap-2.5 border-t border-line pt-5">
                  <Button variant="secondary" onClick={() => setEditing(false)} disabled={saving}>
                    Cancel
                  </Button>
                  <Button type="submit" loading={saving}>
                    {saving ? "Publishing…" : "Save and publish"}
                  </Button>
                </div>
              </form>
            </Card>
          ) : (
            <Card>
              <CardHeader
                title="What your receptionist knows"
                action={
                  canManage && live ? (
                    <Button size="sm" variant="secondary" leadingIcon={<SlidersIcon size={15} />} onClick={startEditing}>
                      Edit
                    </Button>
                  ) : undefined
                }
              />
              <div className="divide-y divide-line">
                <Section title="Services">
                  <ServicesList services={profile?.services ?? []} />
                </Section>
                <Section title="Operating hours">
                  {profile?.hours ? (
                    <HoursTable hours={profile.hours} />
                  ) : (
                    <p className="text-sm text-ink-2">{profile?.hours_raw ?? "No hours were provided."}</p>
                  )}
                </Section>
                <Section title="Greeting">
                  <p className="text-sm text-muted">
                    {greetingStyleLabel(profile?.greeting_style)} style ·{" "}
                    {profile?.custom_greeting ? "your own wording" : "written for you"}
                  </p>
                  {(profile?.greeting ?? profile?.custom_greeting) && (
                    <blockquote className="mt-2 rounded-xl bg-surface-2 px-4 py-3 text-[0.9375rem] leading-relaxed text-ink">
                      “{profile?.greeting ?? profile?.custom_greeting}”
                    </blockquote>
                  )}
                </Section>
                <Section title="Escalation rules">
                  {profile ? <EscalationSummary profile={profile} /> : <p className="text-sm text-muted">—</p>}
                </Section>
              </div>
              {!canManage && (
                <p className="border-t border-line bg-surface-2 px-5 py-3 text-xs text-muted">
                  Only the account&apos;s owners and admins can change the receptionist.
                </p>
              )}
              {canManage && !live && (
                <p className="border-t border-line bg-surface-2 px-5 py-3 text-xs text-muted">
                  You can edit the receptionist once setup has finished.
                </p>
              )}
            </Card>
          )}
        </div>

        <aside className="space-y-6 lg:sticky lg:top-24 lg:self-start">
          <Card className="overflow-hidden">
            <CardHeader title="Preview" description="How your details come across on a call." />
            <div className="p-5">
              <ConversationPreview lines={preview} />
            </div>
          </Card>

          <Card>
            <CardHeader title="Version history" description="Every published configuration is kept." />
            {versions.loading && !versions.data ? (
              <div className="space-y-2 p-5">
                <Skeleton className="h-9 rounded-lg" />
                <Skeleton className="h-9 rounded-lg" />
              </div>
            ) : versions.error ? (
              <p className="p-5 text-sm text-muted">History couldn&apos;t be loaded.</p>
            ) : (
              <ol className="divide-y divide-line">
                {(versions.data ?? []).map((entry) => (
                  <li key={entry.version} className="flex items-center justify-between gap-3 px-5 py-3 text-sm">
                    <span className="min-w-0">
                      <span className="font-semibold">Version {entry.version}</span>
                      <span className="block truncate text-xs text-muted">
                        {formatDateTime(entry.created_at)} · {humanize(entry.generated_by)}
                      </span>
                    </span>
                    {entry.is_live && <Badge tone="success">Live</Badge>}
                  </li>
                ))}
                {versions.data?.length === 0 && (
                  <li className="px-5 py-4 text-sm text-muted">Published during setup.</li>
                )}
              </ol>
            )}
          </Card>
        </aside>
      </div>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="bg-surface px-5 py-4">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1.5 text-sm font-semibold text-ink">{children}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="px-5 py-5">
      <h3 className="mb-2.5 text-xs font-semibold tracking-[0.06em] text-muted uppercase">{title}</h3>
      {children}
    </section>
  );
}
