import { Reveal } from "@/components/ui/Reveal";
import {
  CalendarIcon,
  MailIcon,
  MessageIcon,
  PhoneIncomingIcon,
  UserIcon,
  ZapIcon,
} from "@/components/ui/icons";

import { SectionHeading } from "./SectionHeading";

const CAPABILITIES = [
  {
    icon: PhoneIncomingIcon,
    title: "Answers every call",
    body: "Picks up day and night with a greeting in the style you choose — professional, friendly or formal.",
    example: "“Thanks for calling, how can I help?”",
  },
  {
    icon: MessageIcon,
    title: "Answers business questions",
    body: "Knows your services and opening hours, and handles the questions callers ask most.",
    example: "“Are you open on Saturday?”",
  },
  {
    icon: UserIcon,
    title: "Captures leads",
    body: "Gets the caller’s name, a callback number and what they need, so no inquiry slips away.",
    example: "Name, number and reason — captured",
  },
  {
    icon: CalendarIcon,
    title: "Collects appointment requests",
    body: "Takes down what the caller wants and when it suits them, ready for you to confirm.",
    example: "“Tomorrow afternoon, if possible”",
  },
  {
    icon: MailIcon,
    title: "Takes messages",
    body: "Writes a clear message whenever a caller needs someone on your team to follow up.",
    example: "Message ready for your team",
  },
  {
    icon: ZapIcon,
    title: "Escalates what matters",
    body: "Flags urgent calls and notifies you by email right away, based on rules you set.",
    example: "Urgent call flagged",
  },
] as const;

export function Capabilities() {
  return (
    <section id="features" aria-labelledby="features-title" className="py-20 sm:py-28">
      <div className="container-page">
        <SectionHeading
          id="features-title"
          eyebrow="What it does"
          title="A receptionist that handles the whole call"
          description="From the first hello to the summary in your inbox, every part of the conversation is taken care of."
        />

        <ul className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {CAPABILITIES.map(({ icon: Icon, title, body, example }, index) => (
            <li key={title}>
              <Reveal delay={index * 60} className="h-full">
                <article className="card card-interactive group flex h-full flex-col p-6">
                  <span className="flex size-11 items-center justify-center rounded-xl bg-surface-2 text-ink-2 transition-colors duration-200 group-hover:bg-accent group-hover:text-accent-fg">
                    <Icon size={20} />
                  </span>
                  <h3 className="mt-5 text-[1.0625rem] font-semibold tracking-tight">{title}</h3>
                  <p className="mt-2 flex-1 text-sm leading-relaxed text-muted">{body}</p>
                  <p className="mt-5 border-t border-line pt-4 text-[0.8125rem] font-medium text-subtle transition-colors duration-200 group-hover:text-accent">
                    {example}
                  </p>
                </article>
              </Reveal>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
