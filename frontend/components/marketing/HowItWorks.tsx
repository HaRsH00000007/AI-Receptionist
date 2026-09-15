import type { ReactNode } from "react";

import { ButtonLink } from "@/components/ui/Button";
import { cx } from "@/components/ui/cx";
import { ArrowRightIcon, CheckIcon, PhoneForwardIcon } from "@/components/ui/icons";
import { Reveal } from "@/components/ui/Reveal";

import { SectionHeading } from "./SectionHeading";

const STEPS: ReadonlyArray<{ number: string; title: string; body: string; visual: ReactNode }> = [
  {
    number: "01",
    title: "Choose a number or keep yours",
    body: "Pick an area code for a new local number. Already have a business line? Forward it to your new number and callers keep dialling the one they know.",
    visual: <NumberVisual />,
  },
  {
    number: "02",
    title: "Customize your AI receptionist",
    body: "Tell it about your services, your hours and when a call is urgent, then choose the greeting style that fits your business.",
    visual: <CustomizeVisual />,
  },
  {
    number: "03",
    title: "Go live",
    body: "Review everything, activate, and watch each setup step complete. Your receptionist starts answering as soon as it’s live.",
    visual: <GoLiveVisual />,
  },
];

export function HowItWorks() {
  return (
    <section id="how-it-works" aria-labelledby="how-title" className="py-20 sm:py-28">
      <div className="container-page">
        <SectionHeading
          id="how-title"
          eyebrow="How it works"
          title="Live in three steps"
          description="No hardware, no call center contracts. Setup walks you through everything."
        />

        <ol className="mt-14 grid gap-5 lg:grid-cols-3">
          {STEPS.map((step, index) => (
            <li key={step.number}>
              <Reveal delay={index * 90} className="h-full">
                <article className="card flex h-full flex-col overflow-hidden">
                  <div className="border-b border-line bg-surface-2/70 p-5">{step.visual}</div>
                  <div className="flex flex-1 flex-col p-6">
                    <p className="font-mono text-sm font-semibold text-accent">{step.number}</p>
                    <h3 className="mt-2 text-lg font-semibold tracking-tight">{step.title}</h3>
                    <p className="mt-2 text-sm leading-relaxed text-muted">{step.body}</p>
                  </div>
                </article>
              </Reveal>
            </li>
          ))}
        </ol>

        <div className="mt-10 flex justify-center">
          <ButtonLink href="/get-started" size="lg" trailingIcon={<ArrowRightIcon size={17} />}>
            Start setup
          </ButtonLink>
        </div>
      </div>
    </section>
  );
}

function NumberVisual() {
  return (
    <div aria-hidden className="space-y-2.5">
      <div className="flex gap-2">
        {["415", "212", "805"].map((code) => (
          <span
            key={code}
            className={cx(
              "rounded-lg border px-3 py-1.5 font-mono text-sm",
              code === "805"
                ? "border-accent bg-accent-soft font-semibold text-accent-ink"
                : "border-line bg-surface text-muted",
            )}
          >
            ({code})
          </span>
        ))}
      </div>
      <div className="flex items-center gap-2.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink-2">
        <PhoneForwardIcon size={16} className="text-muted" />
        Forward my existing number
      </div>
    </div>
  );
}

function CustomizeVisual() {
  return (
    <div aria-hidden className="space-y-2.5">
      <div className="grid grid-cols-3 gap-1 rounded-lg border border-line bg-surface p-1 text-center text-xs font-medium">
        <span className="rounded-md px-2 py-1.5 text-muted">Professional</span>
        <span className="rounded-md bg-accent px-2 py-1.5 text-accent-fg">Friendly</span>
        <span className="rounded-md px-2 py-1.5 text-muted">Formal</span>
      </div>
      <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm">
        <span className="text-muted">Hours · </span>
        <span className="text-ink-2">Mon–Fri 9–6, Sat 10–2</span>
      </div>
    </div>
  );
}

function GoLiveVisual() {
  return (
    <div aria-hidden className="space-y-2">
      {["Phone number connected", "Receptionist configured", "Verified and live"].map((label) => (
        <div key={label} className="flex items-center gap-2.5 text-sm text-ink-2">
          <span className="flex size-5 items-center justify-center rounded-full bg-success text-white">
            <CheckIcon size={12} strokeWidth={3} />
          </span>
          {label}
        </div>
      ))}
      <span className="badge badge-success mt-1">
        <span className="badge-dot badge-dot-pulse" />
        Live
      </span>
    </div>
  );
}
