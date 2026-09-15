/**
 * Read-only renderings of a business profile: hours, services, escalation and
 * a conversation preview built from them.
 *
 * The preview is deterministic. It never calls a model and never invents a
 * fact: every reply is assembled from fields the API returned, and the
 * greeting is the live one whenever a configuration is live.
 */

import { cx } from "@/components/ui/cx";
import { formatClock, formatList, formatPhone, humanize } from "@/lib/format";
import {
  WEEKDAYS,
  type BusinessHours,
  type BusinessProfileView,
  type EscalationMode,
  type GreetingStyle,
} from "@/lib/types";

export const GREETING_STYLE_COPY: Record<
  GreetingStyle,
  { label: string; description: string; sample: (businessName: string) => string }
> = {
  professional: {
    label: "Professional",
    description: "Clear, polished and to the point.",
    sample: (name) => `Thank you for calling ${name}. How can I help you today?`,
  },
  friendly: {
    label: "Friendly",
    description: "Warm, upbeat and conversational.",
    sample: (name) => `Hi, thanks for calling ${name}! What can I do for you?`,
  },
  formal: {
    label: "Formal",
    description: "Courteous, measured and traditional.",
    sample: (name) => `Good day, you've reached ${name}. How may I assist you?`,
  },
};

export function greetingStyleLabel(style: string | null | undefined): string {
  if (!style) return "—";
  return GREETING_STYLE_COPY[style as GreetingStyle]?.label ?? humanize(style);
}

export const ESCALATION_MODE_LABELS: Record<EscalationMode, string> = {
  take_message: "Take a message",
  notify_owner: "Notify you right away",
  transfer: "Offer to transfer to a person",
};

export function generatedByLabel(source: string | null | undefined): string {
  switch (source) {
    case "llm":
      return "Written by AI from your details";
    case "template_fallback":
      return "Standard template for your business type";
    case "manual":
      return "Set by our team";
    default:
      return "—";
  }
}

export function HoursTable({ hours }: { hours: BusinessHours }) {
  const byDay = new Map(hours.days.map((entry) => [entry.day, entry]));
  return (
    <dl className="divide-y divide-line overflow-hidden rounded-xl border border-line">
      {WEEKDAYS.map((day) => {
        const entry = byDay.get(day);
        const open = entry && !entry.closed;
        return (
          <div key={day} className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm">
            <dt className="font-medium text-ink-2 capitalize">{day}</dt>
            <dd className={cx("text-right tabular-nums", open ? "font-medium text-ink" : "text-muted")}>
              {!entry
                ? "Not specified"
                : entry.closed
                  ? "Closed"
                  : `${formatClock(entry.opens_at)} – ${formatClock(entry.closes_at)}`}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

export function ServicesList({ services }: { services: readonly string[] }) {
  if (services.length === 0) return <p className="text-sm text-muted">No services were listed.</p>;
  return (
    <ul className="flex flex-wrap gap-2">
      {services.map((service, index) => (
        <li
          key={`${service}-${index}`}
          className="rounded-full border border-line bg-surface-2 px-3 py-1 text-sm font-medium text-ink-2"
        >
          {service}
        </li>
      ))}
    </ul>
  );
}

export function EscalationSummary({ profile }: { profile: BusinessProfileView }) {
  const policy = profile.escalation;
  return (
    <div className="space-y-4">
      {profile.escalation_raw ? (
        <blockquote className="rounded-r-xl border-l-2 border-accent bg-surface-2 px-4 py-3 text-sm leading-relaxed text-ink-2">
          “{profile.escalation_raw}”
        </blockquote>
      ) : (
        <p className="text-sm text-muted">
          No special rules were given, so the receptionist takes a message for anything it can&apos;t
          handle.
        </p>
      )}

      {policy ? (
        <dl className="divide-y divide-line overflow-hidden rounded-xl border border-line text-sm">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 py-2.5">
            <dt className="text-muted">By default</dt>
            <dd className="font-medium text-ink">{ESCALATION_MODE_LABELS[policy.default_mode]}</dd>
          </div>
          {policy.rules.map((rule, index) => (
            <div
              key={`${rule.when}-${index}`}
              className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 py-2.5"
            >
              <dt className="min-w-0 text-muted">When “{rule.when}”</dt>
              <dd className="font-medium text-ink">{ESCALATION_MODE_LABELS[rule.mode]}</dd>
            </div>
          ))}
          {(policy.notify_email || policy.notify_phone) && (
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 py-2.5">
              <dt className="text-muted">Alerts go to</dt>
              <dd className="min-w-0 font-medium break-all text-ink">
                {[policy.notify_email, policy.notify_phone && formatPhone(policy.notify_phone)]
                  .filter(Boolean)
                  .join(" · ")}
              </dd>
            </div>
          )}
        </dl>
      ) : (
        profile.escalation_raw && (
          <p className="text-xs text-muted">
            These rules are turned into structured instructions during setup.
          </p>
        )
      )}
    </div>
  );
}

export interface PreviewLine {
  from: "caller" | "receptionist";
  text: string;
}

export function buildConversationPreview(
  businessName: string,
  profile: BusinessProfileView | null,
): PreviewLine[] {
  const style = profile?.greeting_style ?? "professional";
  const greeting =
    profile?.greeting ??
    (GREETING_STYLE_COPY[style] ?? GREETING_STYLE_COPY.professional).sample(businessName);
  const lines: PreviewLine[] = [{ from: "receptionist", text: greeting }];

  const saturday = profile?.hours?.days.find((entry) => entry.day === "saturday");
  if (saturday) {
    lines.push({ from: "caller", text: "Are you open on Saturday?" });
    lines.push({
      from: "receptionist",
      text: saturday.closed
        ? "We're closed on Saturdays. Would you like me to take a message for the team?"
        : `Yes, on Saturday we're open ${formatClock(saturday.opens_at)} to ${formatClock(saturday.closes_at)}.`,
    });
  } else if (profile?.hours_raw) {
    lines.push({ from: "caller", text: "What are your hours?" });
    lines.push({ from: "receptionist", text: `Our hours are ${profile.hours_raw}.` });
  }

  const services = (profile?.services ?? []).filter(Boolean);
  const first = services[0];
  if (first) {
    lines.push({ from: "caller", text: `Do you offer ${first.toLowerCase()}?` });
    lines.push({
      from: "receptionist",
      text: `We do. We offer ${formatList(services.slice(0, 4).map((service) => service.toLowerCase()))}. Can I take your name and number so the team can follow up?`,
    });
  }

  const mode = profile?.escalation?.rules[0]?.mode ?? profile?.escalation?.default_mode ?? "take_message";
  lines.push({ from: "caller", text: "It's urgent. I need someone to call me back today." });
  lines.push({
    from: "receptionist",
    text:
      mode === "notify_owner"
        ? "I understand. I'll flag this as urgent and let the team know right away. What's the best number to reach you?"
        : "I understand. I'll mark this as urgent and make sure the team gets your message. What's your name and the best number to reach you?",
  });

  return lines;
}

export function ConversationPreview({ lines }: { lines: readonly PreviewLine[] }) {
  return (
    <ol className="space-y-3" aria-label="Example conversation">
      {lines.map((line, index) => (
        <li key={index} className={cx("flex", line.from === "caller" ? "justify-end" : "justify-start")}>
          <div
            className={cx(
              "max-w-[88%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
              line.from === "caller"
                ? "rounded-br-md bg-accent text-accent-fg"
                : "rounded-bl-md border border-line bg-surface-2 text-ink",
            )}
          >
            <span className="mb-0.5 block text-[0.6875rem] font-semibold opacity-75">
              {line.from === "caller" ? "Caller" : "Receptionist"}
            </span>
            {line.text}
          </div>
        </li>
      ))}
    </ol>
  );
}
