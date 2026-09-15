import type { Metadata } from "next";

import { InfoBody, InfoHero, InfoSection } from "@/components/marketing/InfoPage";
import { ButtonLink } from "@/components/ui/Button";
import { BRAND } from "@/lib/brand";

export const metadata: Metadata = { title: "About" };

const PRINCIPLES = [
  {
    title: "Honest about what it does",
    body: "We describe the product as it is. If something isn’t available yet, we don’t imply that it is.",
  },
  {
    title: "Dependable behind the scenes",
    body: "Setup runs as a series of tracked steps. Progress is visible, failures are explained, and steps can be retried safely without buying anything twice.",
  },
  {
    title: "Careful with your data",
    body: "Access to a business’s calls and details is checked on every request, and the dashboard shows summaries rather than raw transcripts.",
  },
] as const;

export default function AboutPage() {
  return (
    <>
      <InfoHero
        eyebrow="About"
        title="Every call deserves an answer"
        intro={`${BRAND.name} gives small and growing businesses an AI receptionist that answers the phone, captures what callers need and keeps owners in the loop — without hiring an answering service.`}
      />
      <InfoBody>
        <InfoSection id="why" title="Why we built it">
          <p>
            For a lot of businesses, the phone is still where customers start. But calls come in while
            you’re with a client, on a job site, or closed for the night — and a missed call is often a
            customer who simply calls someone else.
          </p>
          <p>
            {BRAND.name} answers those calls, has a real conversation using your business’s details, and
            turns each one into a short summary you can act on.
          </p>
        </InfoSection>

        <InfoSection id="principles" title="What we care about">
          <ul className="grid gap-4">
            {PRINCIPLES.map((principle) => (
              <li key={principle.title} className="card p-5">
                <h3 className="font-semibold text-ink">{principle.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted">{principle.body}</p>
              </li>
            ))}
          </ul>
        </InfoSection>

        <div className="flex flex-col gap-3 sm:flex-row">
          <ButtonLink href="/get-started" size="lg">
            Get started
          </ButtonLink>
          <ButtonLink href="/contact" size="lg" variant="secondary">
            Talk to us
          </ButtonLink>
        </div>
      </InfoBody>
    </>
  );
}
