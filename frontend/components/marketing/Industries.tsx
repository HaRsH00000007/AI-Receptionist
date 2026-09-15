import { Badge } from "@/components/ui/Badge";
import {
  HomeIcon,
  KeyIcon,
  LandmarkIcon,
  ScaleIcon,
  ScissorsIcon,
  StethoscopeIcon,
  UmbrellaIcon,
  UtensilsIcon,
  WrenchIcon,
} from "@/components/ui/icons";
import { Reveal } from "@/components/ui/Reveal";

import { SectionHeading } from "./SectionHeading";

/**
 * `tailored` marks the business types setup has a dedicated template for
 * (salon, legal, medical, real estate). Everything else uses the general setup,
 * and the copy below does not claim otherwise.
 */
const INDUSTRIES = [
  {
    icon: HomeIcon,
    name: "Real estate",
    body: "Capture buyer and seller inquiries around the clock, with the property and callback details written down.",
    tailored: true,
  },
  {
    icon: ScaleIcon,
    name: "Law firms",
    body: "Take new-client inquiries after hours with the details your attorneys need to follow up.",
    tailored: true,
  },
  {
    icon: StethoscopeIcon,
    name: "Medical",
    body: "Answer questions about hours and services, collect appointment requests and flag urgent calls for staff.",
    tailored: true,
  },
  {
    icon: ScissorsIcon,
    name: "Salons",
    body: "Handle booking requests and service questions while your stylists stay with the client in the chair.",
    tailored: true,
  },
  {
    icon: WrenchIcon,
    name: "Home services",
    body: "Flag leaks, outages and other emergencies the moment a customer calls, day or night.",
    tailored: false,
  },
  {
    icon: UtensilsIcon,
    name: "Restaurants",
    body: "Answer questions about hours and location, and take messages when the floor is busy.",
    tailored: false,
  },
  {
    icon: KeyIcon,
    name: "Property management",
    body: "Log maintenance requests from tenants and escalate the urgent ones to your team.",
    tailored: false,
  },
  {
    icon: UmbrellaIcon,
    name: "Insurance",
    body: "Capture policy and claim inquiries with a clear summary your agents can act on.",
    tailored: false,
  },
  {
    icon: LandmarkIcon,
    name: "Financial services",
    body: "Greet prospective clients professionally and collect what they need before you call back.",
    tailored: false,
  },
] as const;

export function Industries() {
  return (
    <section id="industries" aria-labelledby="industries-title" className="border-y border-line bg-surface py-20 sm:py-28">
      <div className="container-page">
        <SectionHeading
          id="industries-title"
          eyebrow="Industries"
          title="Made for businesses that live on the phone"
          description="Whatever you do, your receptionist is set up from your own services, hours and rules."
        />

        <ul className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {INDUSTRIES.map(({ icon: Icon, name, body, tailored }, index) => (
            <li key={name}>
              <Reveal delay={(index % 3) * 60} className="h-full">
                <article className="card card-interactive flex h-full gap-4 p-5">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-line bg-bg text-ink-2">
                    <Icon size={19} />
                  </span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-semibold tracking-tight">{name}</h3>
                      {tailored && <Badge tone="accent">Tailored setup</Badge>}
                    </div>
                    <p className="mt-1.5 text-sm leading-relaxed text-muted">{body}</p>
                  </div>
                </article>
              </Reveal>
            </li>
          ))}
        </ul>

        <p className="mt-8 text-center text-sm text-muted">
          Don’t see your industry? Setup works for any business that takes calls.
        </p>
      </div>
    </section>
  );
}
