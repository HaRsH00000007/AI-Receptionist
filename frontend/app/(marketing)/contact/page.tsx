import type { Metadata } from "next";
import Link from "next/link";

import { InfoBody, InfoHero } from "@/components/marketing/InfoPage";
import { ArrowRightIcon, LifebuoyIcon, LockIcon, MailIcon, ZapIcon } from "@/components/ui/icons";
import { BRAND } from "@/lib/brand";

export const metadata: Metadata = { title: "Contact" };

const OPTIONS = [
  {
    href: "/get-started",
    icon: ZapIcon,
    title: "Get started",
    body: "Set up your AI receptionist in a few guided steps.",
  },
  {
    href: "/login",
    icon: LockIcon,
    title: "Sign in",
    body: "Already a customer? We’ll email you a sign-in link.",
  },
  {
    href: "/help",
    icon: LifebuoyIcon,
    title: "Help center",
    body: "Answers about setup, forwarding, summaries and usage.",
  },
] as const;

export default function ContactPage() {
  return (
    <>
      <InfoHero
        eyebrow="Contact"
        title="Talk to us"
        intro="Questions about plans, pricing or whether an AI receptionist fits your business? Here’s how to reach us."
      />
      <InfoBody>
        {BRAND.supportEmail && (
          <a
            href={`mailto:${BRAND.supportEmail}`}
            className="card card-interactive flex items-center gap-4 p-6"
          >
            <span className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-accent text-accent-fg">
              <MailIcon size={22} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm text-muted">Email us</span>
              <span className="block truncate text-lg font-semibold text-ink">{BRAND.supportEmail}</span>
            </span>
            <ArrowRightIcon className="shrink-0 text-muted" />
          </a>
        )}

        <ul className="grid gap-4 sm:grid-cols-3">
          {OPTIONS.map(({ href, icon: Icon, title, body }) => (
            <li key={href}>
              <Link href={href} className="card card-interactive flex h-full flex-col p-5">
                <span className="flex size-9 items-center justify-center rounded-lg bg-accent-soft text-accent">
                  <Icon size={18} />
                </span>
                <span className="mt-4 font-semibold text-ink">{title}</span>
                <span className="mt-1 text-sm leading-relaxed text-muted">{body}</span>
              </Link>
            </li>
          ))}
        </ul>
      </InfoBody>
    </>
  );
}
