import { BuildingIcon, ClockIcon, FileTextIcon, MicIcon, ZapIcon } from "@/components/ui/icons";

const VALUES = [
  { icon: ClockIcon, title: "24/7 answering", body: "Evenings, weekends and holidays." },
  { icon: MicIcon, title: "Natural conversations", body: "A voice that fits your style." },
  { icon: FileTextIcon, title: "Instant call summaries", body: "Ready moments after a call." },
  { icon: ZapIcon, title: "Easy setup", body: "Guided, one step at a time." },
  { icon: BuildingIcon, title: "Business-specific AI", body: "Built from your own details." },
] as const;

export function ValueStrip() {
  return (
    <section aria-labelledby="values-title" className="border-y border-line bg-surface">
      <h2 id="values-title" className="sr-only">
        Why businesses use it
      </h2>
      <ul className="container-page grid grid-cols-1 gap-x-6 gap-y-6 py-8 min-[420px]:grid-cols-2 md:grid-cols-3 lg:grid-cols-5">
        {VALUES.map(({ icon: Icon, title, body }) => (
          <li key={title} className="flex items-start gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
              <Icon size={18} />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold">{title}</p>
              <p className="mt-0.5 text-sm text-muted">{body}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
