"use client";

/**
 * What the receptionist knows, section by section, beside a preview of how it
 * uses it.
 *
 * Read-only: the API has no endpoint for editing a configuration, so this page
 * shows the live state and routes changes to support rather than presenting
 * form fields that would go nowhere.
 */

import type { ReactNode } from "react";

import {
  ConversationPreview,
  EscalationSummary,
  HoursTable,
  ServicesList,
  buildConversationPreview,
  greetingStyleLabel,
} from "@/components/app/BusinessDetails";
import { SupportButton } from "@/components/app/SupportButton";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { Alert } from "@/components/ui/Alert";
import { ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import {
  AlertTriangleIcon,
  BuildingIcon,
  ChevronDownIcon,
  ClockIcon,
  HashIcon,
  MailIcon,
  MessageIcon,
  MicIcon,
  TagIcon,
} from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { formatList, formatPhone } from "@/lib/format";
import { businessTypeLabel } from "@/lib/types";

export function ConfigurationView() {
  const { tenant, profile, phone, agent } = useTenantData();
  const lines = buildConversationPreview(tenant.name, profile);
  const style = greetingStyleLabel(profile?.greeting_style);
  const services = profile?.services ?? [];

  return (
    <div className="space-y-6 lg:space-y-8">
      <PageHeader
        title="Configuration"
        description="What your receptionist knows about your business, and how it handles calls."
      />

      <Alert tone="info" title="Changes are made by our team for now" action={<SupportButton size="sm" label="Request a change" />}>
        Self-serve editing isn&apos;t available in the dashboard yet. Send us what you&apos;d like to
        change and we&apos;ll update your receptionist.
      </Alert>

      {!profile && (
        <Alert tone="warn" title="Some details couldn't be loaded">
          We couldn&apos;t load your business profile, so parts of this page may be incomplete.
        </Alert>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_21rem] xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0 space-y-3">
          <Section
            icon={<BuildingIcon size={18} />}
            title="Business information"
            summary={`${tenant.name} · ${businessTypeLabel(tenant.business_type)}`}
            defaultOpen
          >
            <dl className="grid gap-x-6 gap-y-5 text-sm sm:grid-cols-2">
              <Detail label="Business name">{tenant.name}</Detail>
              <Detail label="Business type">{businessTypeLabel(tenant.business_type)}</Detail>
              <Detail label="Timezone">{tenant.timezone}</Detail>
              <Detail label="Configuration version">
                {agent?.config_version ?? profile?.config_version ?? "Not live yet"}
              </Detail>
            </dl>
          </Section>

          <Section
            icon={<TagIcon size={18} />}
            title="Services"
            summary={services.length ? formatList(services.slice(0, 3)) + (services.length > 3 ? "…" : "") : "None listed"}
          >
            <ServicesList services={services} />
          </Section>

          <Section icon={<ClockIcon size={18} />} title="Operating hours" summary={profile?.hours_raw ?? "Not provided"}>
            {profile?.hours ? (
              <div className="space-y-3">
                <HoursTable hours={profile.hours} />
                <p className="text-xs text-muted">
                  Times are in {profile.hours.timezone}.
                  {profile.hours_raw && <> You wrote: “{profile.hours_raw}”.</>}
                </p>
              </div>
            ) : (
              <p className="text-sm text-ink-2">
                {profile?.hours_raw ? `“${profile.hours_raw}”` : "No hours were provided."}
              </p>
            )}
          </Section>

          <Section
            icon={<MessageIcon size={18} />}
            title="Greeting"
            summary={`${style} style · ${profile?.custom_greeting ? "your wording" : "written for you"}`}
          >
            <div className="space-y-3">
              <span className={profile?.custom_greeting ? "badge badge-accent" : "badge badge-neutral"}>
                {profile?.custom_greeting ? "Your own wording" : "Written for you"}
              </span>
              <p className="text-sm text-muted">
                {profile?.custom_greeting
                  ? `Callers hear this first, exactly as you wrote it, in a ${style.toLowerCase()} voice.`
                  : `Callers hear this first. It's written in a ${style.toLowerCase()} style from your details.`}
              </p>
              {profile?.greeting ? (
                <blockquote className="rounded-xl bg-surface-2 px-4 py-3 text-[0.9375rem] leading-relaxed text-ink">
                  “{profile.greeting}”
                </blockquote>
              ) : (
                <p className="text-sm text-ink-2">
                  {profile?.custom_greeting
                    ? `Your line goes live with your receptionist: “${profile.custom_greeting}”`
                    : "Your greeting is written during setup."}
                </p>
              )}
              {/* The two differ while a newly chosen line is waiting to be published. */}
              {profile?.custom_greeting &&
                profile.greeting &&
                profile.custom_greeting !== profile.greeting && (
                  <p className="text-xs text-muted">
                    Your latest wording goes live with the next configuration: “{profile.custom_greeting}”
                  </p>
                )}
            </div>
          </Section>

          <Section
            icon={<AlertTriangleIcon size={18} />}
            title="Escalation rules"
            summary={profile?.escalation_raw ? "Custom rules" : "Take a message"}
          >
            {profile ? <EscalationSummary profile={profile} /> : <p className="text-sm text-muted">Not available.</p>}
          </Section>

          <Section icon={<MailIcon size={18} />} title="Notification email" summary={tenant.contact_email}>
            <p className="text-sm text-muted">Call summaries and account emails are sent to:</p>
            <p className="mt-2 text-[0.9375rem] font-semibold break-all">{tenant.contact_email}</p>
          </Section>

          <Section
            icon={<HashIcon size={18} />}
            title="Phone configuration"
            summary={phone ? formatPhone(phone.e164) : "Not assigned yet"}
          >
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <dl className="grid grid-cols-2 gap-x-8 gap-y-4 text-sm">
                <Detail label="Number">{phone ? formatPhone(phone.e164) : "Not assigned yet"}</Detail>
                <Detail label="Area code">{phone?.area_code ?? tenant.area_code ?? "—"}</Detail>
              </dl>
              <ButtonLink href="/dashboard/phone" variant="secondary" size="sm">
                Forwarding setup
              </ButtonLink>
            </div>
          </Section>

          <Section icon={<MicIcon size={18} />} title="AI voice" summary={`Matched to your ${style.toLowerCase()} style`}>
            <p className="text-sm leading-relaxed text-muted">
              Your receptionist&apos;s voice is chosen to suit the greeting style you picked, so the
              way it sounds matches the way it speaks.
            </p>
          </Section>
        </div>

        <aside className="lg:sticky lg:top-24 lg:self-start">
          <Card className="overflow-hidden">
            <CardHeader title="Live preview" description="Here's how your receptionist will respond." />
            <div className="p-5">
              <ConversationPreview lines={lines} />
            </div>
            <p className="border-t border-line bg-surface-2 px-5 py-3 text-xs leading-relaxed text-muted">
              {profile?.greeting
                ? "The greeting is your live one. The replies show how your details are used; on a real call the receptionist words things naturally."
                : "Built from your details. Your live greeting is written during setup, and on a real call the receptionist words things naturally."}
            </p>
          </Card>
        </aside>
      </div>
    </div>
  );
}

function Section({
  icon,
  title,
  summary,
  defaultOpen = false,
  children,
}: {
  icon: ReactNode;
  title: string;
  summary: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <details className="card group overflow-hidden" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center gap-4 px-4 py-4 transition-colors hover:bg-surface-2 sm:px-5 [&::-webkit-details-marker]:hidden">
        <span className="choice-icon">{icon}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-[0.9375rem] font-semibold">{title}</span>
          <span className="block truncate text-sm text-muted">{summary}</span>
        </span>
        <ChevronDownIcon className="shrink-0 text-muted transition-transform group-open:rotate-180" />
      </summary>
      <div className="border-t border-line px-4 py-5 sm:px-5">{children}</div>
    </details>
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
