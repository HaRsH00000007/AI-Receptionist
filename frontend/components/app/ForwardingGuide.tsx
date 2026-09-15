/**
 * How to keep an existing business number.
 *
 * Forwarding is configured with the business's own carrier, not with us, so
 * this is guidance rather than a form: we provision a dedicated AI line and the
 * existing number forwards to it. Carrier codes vary; the one given is only
 * described as common, never as universal.
 */

import { CopyButton } from "@/components/ui/CopyButton";
import { PhoneForwardIcon } from "@/components/ui/icons";
import { formatPhone } from "@/lib/format";

export function ForwardingGuide({ number }: { number: string | null }) {
  const steps = [
    {
      title: "Contact your phone carrier",
      body: "Call forwarding is set up with the provider of your existing business line — usually in their app, online account or by calling support.",
    },
    {
      title: "Forward calls to your AI number",
      body: number
        ? `Forward to ${formatPhone(number)}. On many US carriers you can dial *72 followed by the number; codes vary, so check your carrier's instructions.`
        : "You'll get your AI number as soon as setup finishes.",
    },
    {
      title: "Choose when calls forward",
      body: "Forward every call, or only calls you don't pick up (often called “no answer” or “busy” forwarding) to use your receptionist as a backup.",
    },
    {
      title: "Place a test call",
      body: "Call your existing number from another phone and check that your receptionist answers.",
    },
  ];

  return (
    <section aria-labelledby="forwarding-heading" className="card overflow-hidden">
      <div className="flex flex-col gap-4 border-b border-line px-5 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex items-start gap-3.5">
          <span className="choice-icon">
            <PhoneForwardIcon size={18} />
          </span>
          <div>
            <h2 id="forwarding-heading" className="text-[0.9375rem] font-semibold">
              Keep your existing business number
            </h2>
            <p className="mt-0.5 text-sm text-muted">
              Forward it to your AI line. Callers keep dialing the number they already know.
            </p>
          </div>
        </div>
        {number && <CopyButton value={number} label="Copy AI number" />}
      </div>
      <ol className="grid gap-px bg-line sm:grid-cols-2">
        {steps.map((step, index) => (
          <li key={step.title} className="flex gap-3.5 bg-surface px-5 py-5 sm:px-6">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-bold text-accent-ink">
              {index + 1}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold">{step.title}</p>
              <p className="mt-1 text-sm leading-relaxed text-muted">{step.body}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
