import type { ReactNode } from "react";

import { cx } from "@/components/ui/cx";
import { CheckIcon } from "@/components/ui/icons";
import { Reveal } from "@/components/ui/Reveal";

import {
  AfterHoursMockup,
  CallsListMockup,
  KnowledgeMockup,
  RulesMockup,
  StatusMockup,
  SummaryEmailMockup,
} from "./mockups";
import { SectionHeading } from "./SectionHeading";

const FEATURES: ReadonlyArray<{
  id: string;
  eyebrow: string;
  title: string;
  body: string;
  points: readonly string[];
  visual: ReactNode;
}> = [
  {
    id: "answering",
    eyebrow: "Answering",
    title: "Every call handled",
    body: "Your receptionist answers every call with a greeting in your style, day or night. Each conversation lands in your dashboard with who called and why.",
    points: [
      "Answers around the clock, including weekends and holidays",
      "Greets callers in a professional, friendly or formal style",
      "Every call listed with caller, intent and status",
    ],
    visual: <CallsListMockup />,
  },
  {
    id: "knowledge",
    eyebrow: "Knowledge",
    title: "Your AI knows your business",
    body: "Setup turns your services, hours and details into instructions and answers to common questions, so callers get accurate information.",
    points: [
      "Built from the details you enter during setup",
      "Knows when you’re open and what you offer",
      "Handles the questions callers ask most",
    ],
    visual: <KnowledgeMockup />,
  },
  {
    id: "escalation",
    eyebrow: "Escalation",
    title: "Escalates what matters",
    body: "Describe what counts as urgent in plain language. When a call matches, it’s flagged and you’re notified by email right away.",
    points: [
      "Rules written in your own words",
      "Urgent calls flagged in your dashboard",
      "Everything else captured as a clear message",
    ],
    visual: <RulesMockup />,
  },
  {
    id: "summaries",
    eyebrow: "Summaries",
    title: "Instant call summaries",
    body: "Moments after a call ends, a summary is ready in your dashboard and sent to your notification email.",
    points: [
      "Caller name, callback number and intent",
      "Urgency noted on every call",
      "Callback numbers marked as stated by the caller",
    ],
    visual: <SummaryEmailMockup />,
  },
  {
    id: "after-hours",
    eyebrow: "After hours",
    title: "Built for after-hours calls",
    body: "The calls you would otherwise miss — evenings, weekends, early mornings — are answered, captured and waiting for you.",
    points: [
      "Callers reach a receptionist, not a voicemail greeting",
      "Urgent issues escalated even while you’re closed",
      "Start the day with every message summarized",
    ],
    visual: <AfterHoursMockup />,
  },
  {
    id: "dashboard",
    eyebrow: "Dashboard",
    title: "Everything at a glance",
    body: "See your receptionist’s status, your number, recent calls and how many minutes you’ve used this billing period.",
    points: [
      "Live status for your receptionist",
      "Minutes used against your plan",
      "A warning before you reach your limit",
    ],
    visual: <StatusMockup />,
  },
];

export function Features() {
  return (
    <section id="product" aria-labelledby="product-title" className="py-20 sm:py-28">
      <div className="container-page">
        <SectionHeading
          id="product-title"
          eyebrow="Product"
          title="Everything you need to never miss a lead"
          description="Built around the moments that cost businesses customers: the missed call, the lost message, the urgent issue nobody heard about."
        />

        <div className="mt-16 space-y-20 sm:space-y-28">
          {FEATURES.map((feature, index) => (
            <article
              key={feature.id}
              aria-labelledby={`feature-${feature.id}`}
              className="grid items-center gap-10 lg:grid-cols-2 lg:gap-16"
            >
              <Reveal className={cx("min-w-0", index % 2 === 1 && "lg:order-last")}>
                <p className="eyebrow">{feature.eyebrow}</p>
                <h3 id={`feature-${feature.id}`} className="heading-lg mt-3">
                  {feature.title}
                </h3>
                <p className="mt-4 max-w-lg text-[1.0625rem] leading-relaxed text-muted">{feature.body}</p>
                <ul className="mt-6 space-y-3">
                  {feature.points.map((point) => (
                    <li key={point} className="flex items-start gap-3 text-[0.9375rem] text-ink-2">
                      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent">
                        <CheckIcon size={12} strokeWidth={2.6} />
                      </span>
                      {point}
                    </li>
                  ))}
                </ul>
              </Reveal>
              <Reveal delay={100} className="min-w-0">
                <div className="rounded-[24px] border border-line bg-surface-2/70 p-4 sm:p-8">
                  <div className="mx-auto max-w-md">{feature.visual}</div>
                </div>
              </Reveal>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
