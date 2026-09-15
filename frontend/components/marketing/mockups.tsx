/**
 * Product mockups for the feature sections.
 *
 * Every field drawn here exists in the product: calls with caller, intent and
 * status; the business details setup collects; escalation rules; summaries;
 * minutes against the plan. Names and numbers are fictional.
 */

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";
import { cx } from "@/components/ui/cx";
import {
  BellIcon,
  CheckIcon,
  ClockIcon,
  HeadsetIcon,
  MailIcon,
  MoonStarIcon,
  PhoneIcon,
} from "@/components/ui/icons";
import { BRAND } from "@/lib/brand";

function MockFrame({
  label,
  title,
  meta,
  children,
  className,
}: {
  label: string;
  title: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div role="img" aria-label={label} className={cx("w-full min-w-0", className)}>
      <div className="card overflow-hidden rounded-[18px] shadow-lg">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
          <p className="truncate text-sm font-semibold">{title}</p>
          {meta}
        </div>
        {children}
      </div>
    </div>
  );
}

const CALLS = [
  { initials: "MR", name: "Maya Reyes", intent: "Appointment request", time: "8:14 AM", urgent: false },
  { initials: "CA", name: "Chris Allen", intent: "Pricing question", time: "7:52 AM", urgent: false },
  { initials: "PN", name: "Priya Nair", intent: "Emergency repair", time: "Yesterday", urgent: true },
  { initials: "TW", name: "Tom Walsh", intent: "Message for owner", time: "Yesterday", urgent: false },
] as const;

export function CallsListMockup() {
  return (
    <MockFrame label="Illustration: a list of recent calls with caller, intent and status" title="Recent calls" meta={<Badge tone="neutral">Today</Badge>}>
      <ul className="divide-y divide-line">
        {CALLS.map((call) => (
          <li key={call.name} className="flex items-center gap-3 px-4 py-3.5 sm:px-5">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-surface-2 text-xs font-semibold text-ink-2">
              {call.initials}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">{call.name}</p>
              <p className="truncate text-xs text-muted">{call.intent}</p>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              <span className="text-xs text-muted">{call.time}</span>
              {call.urgent ? <Badge tone="warn">Urgent</Badge> : <Badge tone="success">Summary sent</Badge>}
            </div>
          </li>
        ))}
      </ul>
    </MockFrame>
  );
}

export function KnowledgeMockup() {
  return (
    <MockFrame label="Illustration: the business details the receptionist uses — services, hours and greeting" title="Northside Dental" meta={<Badge tone="accent">Friendly</Badge>}>
      <div className="space-y-5 p-4 sm:p-5">
        <div>
          <p className="text-xs font-semibold tracking-wider text-muted uppercase">Services</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {["Cleanings", "Whitening", "Emergency visits", "Invisalign consults"].map((service) => (
              <span key={service} className="rounded-full border border-line bg-surface-2 px-2.5 py-1 text-xs font-medium text-ink-2">
                {service}
              </span>
            ))}
          </div>
        </div>
        <div>
          <p className="text-xs font-semibold tracking-wider text-muted uppercase">Hours</p>
          <dl className="mt-2 divide-y divide-line rounded-lg border border-line text-sm">
            {[
              ["Mon – Thu", "8:00 – 6:00"],
              ["Friday", "8:00 – 3:00"],
              ["Sat – Sun", "Closed"],
            ].map(([day, hours]) => (
              <div key={day} className="flex justify-between gap-3 px-3 py-2">
                <dt className="text-muted">{day}</dt>
                <dd className="font-medium text-ink-2">{hours}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="rounded-lg bg-accent-soft px-3.5 py-3 text-sm text-accent-ink">
          “Good morning, Northside Dental. How can I help you today?”
        </div>
      </div>
    </MockFrame>
  );
}

export function RulesMockup() {
  const rules = [
    { when: "Caller mentions pain or an emergency", then: "Notify me by email", tone: "warn" as const },
    { when: "A new patient asks about pricing", then: "Take a message", tone: "neutral" as const },
    { when: "Anything else", then: "Take a message", tone: "neutral" as const },
  ];
  return (
    <MockFrame label="Illustration: escalation rules deciding which calls notify the owner" title="Escalation rules">
      <ul className="space-y-2.5 p-4 sm:p-5">
        {rules.map((rule) => (
          <li key={rule.when} className="flex flex-col gap-2 rounded-lg border border-line bg-surface-2/60 p-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-ink-2">
              <span className="text-muted">When </span>
              {rule.when.charAt(0).toLowerCase() + rule.when.slice(1)}
            </p>
            <Badge tone={rule.tone} className="self-start sm:self-auto">
              {rule.then}
            </Badge>
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-3 border-t border-line px-4 py-3.5 sm:px-5">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn">
          <BellIcon size={16} />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">Urgent call flagged</p>
          <p className="truncate text-xs text-muted">Emailed to the owner · just now</p>
        </div>
      </div>
    </MockFrame>
  );
}

export function SummaryEmailMockup() {
  return (
    <MockFrame label="Illustration: a call summary email" title="Inbox" meta={<MailIcon size={16} className="text-muted" />}>
      <div className="p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent text-accent-fg">
            <HeadsetIcon size={16} />
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{BRAND.name}</p>
            <p className="truncate text-sm text-ink-2">New call: Daniel Kim · Appointment request</p>
          </div>
        </div>
        <div className="mt-4 rounded-xl border border-line p-4">
          <p className="text-sm leading-relaxed text-ink-2">
            Daniel would like a cleaning next week, ideally on a weekday morning. He is a new
            patient and asked for a call back.
          </p>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
            <div>
              <dt className="text-muted">Callback</dt>
              <dd className="mt-0.5 font-mono text-ink-2">(555) 013-4478</dd>
            </div>
            <div>
              <dt className="text-muted">Urgency</dt>
              <dd className="mt-0.5 font-medium text-ink-2">Routine</dd>
            </div>
          </dl>
        </div>
      </div>
    </MockFrame>
  );
}

export function AfterHoursMockup() {
  const events = [
    { time: "9:42 PM", title: "Sam Ortiz · New inquiry", result: "Message taken", urgent: false },
    { time: "11:18 PM", title: "Alex Morgan · Water leak", result: "Owner notified", urgent: true },
    { time: "6:05 AM", title: "Lee Park · Opening hours", result: "Question answered", urgent: false },
  ];
  return (
    <MockFrame
      label="Illustration: calls answered overnight while the business was closed"
      title="While you were closed"
      meta={<MoonStarIcon size={16} className="text-muted" />}
    >
      <ol className="relative space-y-4 p-4 sm:p-5">
        {events.map((event) => (
          <li key={event.time} className="flex gap-3">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-line bg-surface-2 text-muted">
              <PhoneIcon size={14} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                <p className="truncate text-sm font-semibold">{event.title}</p>
                <span className="flex items-center gap-1 text-xs text-muted">
                  <ClockIcon size={12} />
                  {event.time}
                </span>
              </div>
              <p className={cx("mt-0.5 text-xs font-medium", event.urgent ? "text-warn" : "text-success")}>
                {event.result}
              </p>
            </div>
          </li>
        ))}
      </ol>
    </MockFrame>
  );
}

export function StatusMockup() {
  return (
    <MockFrame
      label="Illustration: the dashboard showing a live receptionist and minutes used this period"
      title="Overview"
      meta={
        <Badge tone="success" dot pulse>
          Live
        </Badge>
      }
    >
      <div className="p-4 sm:p-5">
        <p className="text-xs text-muted">Your number</p>
        <p className="mt-1 font-mono text-lg font-semibold tracking-tight">+1 (555) 010-2468</p>
        <div className="mt-5 grid grid-cols-2 gap-3">
          <div className="rounded-lg border border-line p-3">
            <p className="text-xs text-muted">Calls this period</p>
            <p className="mt-1 text-xl font-bold">48</p>
          </div>
          <div className="rounded-lg border border-line p-3">
            <p className="text-xs text-muted">Minutes used</p>
            <p className="mt-1 text-xl font-bold">
              212 <span className="text-sm font-medium text-muted">/ 500</span>
            </p>
          </div>
        </div>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-surface-3">
          <div className="h-full w-[42%] rounded-full bg-accent" />
        </div>
        <p className="mt-2 flex items-center gap-1.5 text-xs text-muted">
          <CheckIcon size={13} className="text-success" />
          Well within your plan
        </p>
      </div>
    </MockFrame>
  );
}
