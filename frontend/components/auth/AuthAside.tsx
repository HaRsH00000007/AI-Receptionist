import { CheckCircleIcon } from "@/components/ui/icons";
import { BRAND } from "@/lib/brand";

const POINTS = [
  "A summary of every call, moments after it ends",
  "Urgent calls flagged and sent to your inbox",
  "Setup status, calls and usage in one place",
];

/** The illustrative half of the sign-in screen. The call shown is an example. */
export function AuthAside() {
  return (
    <aside className="relative hidden overflow-hidden bg-night text-night-ink lg:flex lg:flex-col lg:justify-between lg:gap-12 lg:p-12 xl:p-16">
      <div
        aria-hidden
        className="pointer-events-none absolute -top-48 -right-48 size-[34rem] rounded-full bg-[#3651f0]/20 blur-3xl"
      />

      <div className="relative">
        <p className="text-sm font-semibold text-night-muted">{BRAND.name} for business</p>
        <h2 className="mt-4 max-w-md text-[2rem] leading-tight font-bold tracking-tight">
          Every call answered. Every caller followed up.
        </h2>
      </div>

      <div className="relative w-full max-w-md">
        <div className="animate-float rounded-2xl border border-night-line bg-night-2 p-5 shadow-xl">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-2 text-xs font-semibold text-night-muted">
              <span className="size-1.5 rounded-full bg-emerald-400" />
              Call summary
            </span>
            <span className="rounded-full border border-night-line px-2 py-0.5 text-[0.6875rem] text-night-muted">
              Example
            </span>
          </div>
          <p className="mt-4 text-base font-semibold">Jordan P. · Booking request</p>
          <p className="mt-2 text-sm leading-relaxed text-night-muted">
            Wants a consultation next Tuesday afternoon and asked for a call back after 3 PM.
          </p>
          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-night-3 px-2.5 py-1">Callback +1 (555) 010-2233</span>
            <span className="rounded-full bg-night-3 px-2.5 py-1">2m 14s</span>
            <span className="rounded-full bg-amber-400/15 px-2.5 py-1 text-amber-300">Urgency 3 of 5</span>
          </div>
        </div>
      </div>

      <ul className="relative space-y-3 text-sm text-night-muted">
        {POINTS.map((point) => (
          <li key={point} className="flex items-center gap-3">
            <CheckCircleIcon size={18} className="shrink-0 text-[#a3b0ff]" />
            {point}
          </li>
        ))}
      </ul>
    </aside>
  );
}
