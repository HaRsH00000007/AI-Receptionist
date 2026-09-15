import type { Metadata } from "next";
import Link from "next/link";

import { InfoBody, InfoHero, InfoSection } from "@/components/marketing/InfoPage";
import { ButtonLink } from "@/components/ui/Button";
import {
  BarChartIcon,
  ChevronDownIcon,
  FileTextIcon,
  LockIcon,
  PhoneIcon,
  ZapIcon,
} from "@/components/ui/icons";

export const metadata: Metadata = { title: "Help center" };

const TOPICS = [
  { href: "#getting-started", icon: ZapIcon, title: "Getting started", body: "Set up your receptionist." },
  { href: "#phone-number", icon: PhoneIcon, title: "Your phone number", body: "New number or forwarding." },
  { href: "#calls", icon: FileTextIcon, title: "Calls and summaries", body: "What you get after a call." },
  { href: "#usage", icon: BarChartIcon, title: "Usage and plans", body: "How minutes are counted." },
  { href: "#signing-in", icon: LockIcon, title: "Signing in", body: "Access your dashboard." },
] as const;

const FAQ = [
  {
    question: "How long does setup take?",
    answer:
      "Filling in your details takes a few minutes. After you activate, you’ll see each setup step complete in real time — validating your details, writing your receptionist’s configuration, reserving your number, creating the receptionist, connecting and verifying it.",
  },
  {
    question: "Can I keep my existing business number?",
    answer:
      "Yes. Setup gives your receptionist its own local number, and you forward your existing line to it through your phone carrier. Callers keep dialling the number they already know.",
  },
  {
    question: "What does the receptionist say when it answers?",
    answer:
      "It greets callers using your business name in the style you choose — professional, friendly or formal — and uses the services, hours and rules you entered during setup.",
  },
  {
    question: "What happens with urgent calls?",
    answer:
      "During setup you describe what counts as urgent in your own words. Calls are scored for urgency, urgent ones are flagged in your dashboard, and your escalation rules decide when you’re notified by email.",
  },
  {
    question: "Can I listen to recordings or read transcripts?",
    answer:
      "The dashboard shows an AI summary of each call rather than a recording or full transcript: who called, how to reach them, what they wanted, how urgent it was and a short written summary.",
  },
  {
    question: "What happens if I go over my plan’s minutes?",
    answer:
      "You’ll see a warning as you approach your limit. Paid plans keep answering calls if you go over; only the free trial stops taking calls once its included minutes are used.",
  },
] as const;

export default function HelpPage() {
  return (
    <>
      <InfoHero
        eyebrow="Help center"
        title="How your AI receptionist works"
        intro="Everything you need to set up your receptionist, understand your calls and manage your account."
      />
      <InfoBody>
        <nav aria-label="Help topics">
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {TOPICS.map(({ href, icon: Icon, title, body }) => (
              <li key={href}>
                <Link href={href} className="card card-interactive flex h-full items-start gap-3 p-4">
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
                    <Icon size={17} />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold text-ink">{title}</span>
                    <span className="mt-0.5 block text-sm text-muted">{body}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <InfoSection id="getting-started" title="Getting started">
          <p>Setup is a short guided flow:</p>
          <ol className="list-decimal space-y-2 pl-5 marker:font-semibold marker:text-accent">
            <li>
              <strong className="text-ink">Business</strong> — your business name, type, services and a
              contact phone number.
            </li>
            <li>
              <strong className="text-ink">Receptionist</strong> — your greeting style, opening hours,
              what counts as urgent, and the email address that receives call summaries.
            </li>
            <li>
              <strong className="text-ink">Phone</strong> — choose an area code for a new local number, or
              plan to forward your existing number.
            </li>
            <li>
              <strong className="text-ink">Review</strong> — check everything and choose a plan.
            </li>
            <li>
              <strong className="text-ink">Activate</strong> — watch each setup step complete. If a step
              needs attention, you’ll see what happened in plain language.
            </li>
          </ol>
          <div>
            <ButtonLink href="/get-started">Start setup</ButtonLink>
          </div>
        </InfoSection>

        <InfoSection id="phone-number" title="Your phone number">
          <p>
            Your receptionist answers on a local number in the area code you choose. If that area code has
            no numbers available, setup will tell you so you can pick a nearby one.
          </p>
          <p>
            <strong className="text-ink">Keeping your existing number.</strong> Once your receptionist is
            live, forward your current business line to your new number through your phone carrier.
            Forwarding codes vary by carrier: on many US carriers you dial <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-sm">*72</code>{" "}
            followed by the number you’re forwarding to. Check your carrier’s instructions for the exact
            steps, including forwarding only when you don’t answer.
          </p>
        </InfoSection>

        <InfoSection id="calls" title="Calls and summaries">
          <p>
            After each call, the receptionist writes a summary that appears in your dashboard and is sent
            to your notification email. Each summary includes:
          </p>
          <ul className="list-disc space-y-1.5 pl-5">
            <li>The caller’s name, when they give it</li>
            <li>A callback number, marked as stated by the caller — it isn’t verified</li>
            <li>What the caller wanted (their intent)</li>
            <li>How urgent the call was</li>
            <li>A short written summary of the conversation</li>
          </ul>
        </InfoSection>

        <InfoSection id="usage" title="Usage and plans">
          <p>
            Usage is measured in call minutes for each billing period and compared with the minutes your
            plan includes. Your dashboard shows minutes used, calls handled and the dates of the current
            period, and warns you as you approach your limit.
          </p>
          <p>
            Paid plans keep answering calls if you go over your included minutes. The free trial is the only
            plan that stops taking calls at its limit.
          </p>
        </InfoSection>

        <InfoSection id="signing-in" title="Signing in">
          <p>
            There’s no password. Enter the email address on your account and we’ll email you a single-use
            sign-in link. The link works once and expires after a short time. For your privacy, the sign-in
            page looks the same whether or not an address has an account.
          </p>
          <p>
            <Link href="/login" className="link">
              Go to sign in
            </Link>
          </p>
        </InfoSection>

        <section id="faq" aria-labelledby="faq-title">
          <h2 id="faq-title" className="heading-lg">
            Frequently asked questions
          </h2>
          <div className="card mt-5 divide-y divide-line">
            {FAQ.map((item) => (
              <details key={item.question} className="group px-5 py-1">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 py-4 text-[0.9375rem] font-semibold text-ink [&::-webkit-details-marker]:hidden">
                  {item.question}
                  <ChevronDownIcon size={18} className="shrink-0 text-muted transition-transform group-open:rotate-180" />
                </summary>
                <p className="pb-5 text-[0.9375rem] leading-relaxed text-muted">{item.answer}</p>
              </details>
            ))}
          </div>
        </section>
      </InfoBody>
    </>
  );
}
