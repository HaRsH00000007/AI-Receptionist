import { Badge } from "@/components/ui/Badge";
import { CheckCircleIcon, FileTextIcon } from "@/components/ui/icons";
import { Reveal } from "@/components/ui/Reveal";

import { SectionHeading } from "./SectionHeading";

const FIELDS: ReadonlyArray<{ label: string; value: string; mono?: boolean; note?: string }> = [
  { label: "Caller", value: "Daniel Kim" },
  { label: "Phone number", value: "+1 (555) 013-4478", mono: true },
  { label: "Callback number", value: "+1 (555) 013-4478", mono: true, note: "as stated by caller" },
  { label: "Time", value: "Today, 10:32 AM" },
  { label: "Duration", value: "2m 04s" },
  { label: "Intent", value: "Appointment request" },
];

export function CallSummaryShowcase() {
  return (
    <section aria-labelledby="summary-title" className="border-y border-line bg-surface py-20 sm:py-28">
      <div className="container-page grid items-center gap-12 lg:grid-cols-[0.9fr_1.1fr] lg:gap-16">
        <div>
          <SectionHeading
            id="summary-title"
            align="start"
            eyebrow="Call summaries"
            title="A summary you can act on in seconds"
            description="Skip the voicemail and the replay. Every call becomes a short, structured summary with the details you need to call back."
          />
          <ul className="mt-8 space-y-4 text-[0.9375rem] text-ink-2">
            <li className="flex gap-3">
              <CheckCircleIcon size={20} className="shrink-0 text-success" />
              Who called, how to reach them and what they wanted
            </li>
            <li className="flex gap-3">
              <CheckCircleIcon size={20} className="shrink-0 text-success" />
              Urgent calls clearly marked
            </li>
            <li className="flex gap-3">
              <CheckCircleIcon size={20} className="shrink-0 text-success" />
              Sent to your notification email as soon as it’s ready
            </li>
          </ul>
        </div>

        <Reveal className="min-w-0">
          <div
            role="img"
            aria-label="Illustration: a call summary with caller, numbers, time, duration, intent, urgency and a written summary"
            className="card overflow-hidden rounded-[20px] shadow-xl"
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4 sm:px-6">
              <div className="flex items-center gap-3">
                <span className="flex size-9 items-center justify-center rounded-lg bg-accent-soft text-accent">
                  <FileTextIcon size={18} />
                </span>
                <div>
                  <p className="text-sm font-semibold">Call summary</p>
                  <p className="text-xs text-muted">Harbor Street Salon</p>
                </div>
              </div>
              <Badge tone="neutral">Routine</Badge>
            </div>

            <dl className="grid grid-cols-1 gap-x-6 gap-y-4 px-5 py-5 min-[460px]:grid-cols-2 sm:px-6">
              {FIELDS.map((field) => (
                <div key={field.label} className="min-w-0">
                  <dt className="text-xs font-medium text-muted">{field.label}</dt>
                  <dd className={field.mono ? "mt-1 truncate font-mono text-sm text-ink" : "mt-1 truncate text-sm font-medium text-ink"}>
                    {field.value}
                  </dd>
                  {field.note && <dd className="mt-0.5 text-xs text-subtle italic">{field.note}</dd>}
                </div>
              ))}
            </dl>

            <div className="border-t border-line px-5 py-5 sm:px-6">
              <p className="text-xs font-medium text-muted">Summary</p>
              <p className="mt-1.5 text-[0.9375rem] leading-relaxed text-ink-2">
                Daniel wants a haircut and beard trim on Friday, any time after 3 PM. He’s a returning
                client and prefers to see the same stylist as last time. Asked for a call back to
                confirm.
              </p>
            </div>

            <div className="flex items-center gap-2 border-t border-line bg-success-soft px-5 py-3 text-sm font-medium text-success-ink sm:px-6">
              <CheckCircleIcon size={16} />
              Summary emailed to your notification address
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
