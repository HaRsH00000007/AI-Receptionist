import type { Metadata } from "next";

import { InfoBody, InfoHero, InfoSection } from "@/components/marketing/InfoPage";
import {
  FileTextIcon,
  KeyIcon,
  LockIcon,
  MailIcon,
  ShieldCheckIcon,
  UsersIcon,
  ActivityIcon,
  CheckCircleIcon,
} from "@/components/ui/icons";
import { BRAND } from "@/lib/brand";

export const metadata: Metadata = { title: "Security" };

const PRACTICES = [
  {
    icon: MailIcon,
    title: "Passwordless sign-in",
    body: "You sign in with a single-use link sent to your email. Each link works once and expires after a short time — 15 minutes by default — so there is no password to reuse, guess or leak.",
  },
  {
    icon: LockIcon,
    title: "Sessions scripts can’t read",
    body: "Your session lives in an HttpOnly cookie. JavaScript running in the page cannot read it, which keeps it out of reach of injected scripts.",
  },
  {
    icon: UsersIcon,
    title: "No account lookups",
    body: "The sign-in form responds the same way whether or not an address has an account, so it can’t be used to find out who our customers are.",
  },
  {
    icon: ShieldCheckIcon,
    title: "Access checked on every request",
    body: "Every request for a business’s data is authorized on our servers against your membership of that business. Knowing an account’s identifier opens nothing on its own.",
  },
  {
    icon: KeyIcon,
    title: "Expiring setup links",
    body: "The setup progress page you see right after signing up is opened by a signed grant that expires, rather than by an identifier that would work forever.",
  },
  {
    icon: CheckCircleIcon,
    title: "Verified provider webhooks",
    body: "Events from our telephone, voice and billing providers are signature-verified before they’re accepted, and duplicate deliveries are recognized and ignored.",
  },
  {
    icon: ActivityIcon,
    title: "Controlled, audited operations",
    body: "Administrative actions such as retrying or cancelling a setup require an operator key and are recorded in an audit log.",
  },
  {
    icon: FileTextIcon,
    title: "Summaries, not transcripts",
    body: "Your dashboard shows AI summaries of calls. Raw conversation transcripts are not exposed through the dashboard or its API.",
  },
] as const;

export default function SecurityPage() {
  return (
    <>
      <InfoHero
        eyebrow="Security"
        title="Built to protect your business and your callers"
        intro={`${BRAND.name} handles phone calls from your customers, so security is part of how the product is built, not an add-on. Here is what that means in practice.`}
      />
      <InfoBody>
        <InfoSection id="practices" title="How we protect your account">
          <ul className="grid gap-4 sm:grid-cols-2">
            {PRACTICES.map(({ icon: Icon, title, body }) => (
              <li key={title} className="card p-5">
                <span className="flex size-9 items-center justify-center rounded-lg bg-accent-soft text-accent">
                  <Icon size={18} />
                </span>
                <h3 className="mt-4 font-semibold text-ink">{title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted">{body}</p>
              </li>
            ))}
          </ul>
        </InfoSection>

        <InfoSection id="report" title="Reporting a concern">
          <p>
            If you believe you’ve found a security issue, please contact us
            {BRAND.supportEmail ? (
              <>
                {" "}
                at{" "}
                <a className="link" href={`mailto:${BRAND.supportEmail}`}>
                  {BRAND.supportEmail}
                </a>
              </>
            ) : (
              " through our contact page"
            )}
            . Please include enough detail for us to reproduce what you saw.
          </p>
        </InfoSection>
      </InfoBody>
    </>
  );
}
