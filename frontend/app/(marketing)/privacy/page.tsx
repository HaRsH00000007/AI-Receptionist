import type { Metadata } from "next";

import { FactList, InfoBody, InfoHero, InfoSection } from "@/components/marketing/InfoPage";
import { Alert } from "@/components/ui/Alert";
import { BRAND } from "@/lib/brand";

export const metadata: Metadata = { title: "Privacy" };

const STORED = [
  {
    term: "Business details",
    detail:
      "What you submit during setup: business name and type, services, opening hours, greeting style, escalation rules, and the configuration generated from them.",
  },
  {
    term: "Contact information",
    detail: "Your notification email address and contact phone number.",
  },
  {
    term: "Phone number",
    detail: "The number assigned to your receptionist and when it was reserved.",
  },
  {
    term: "Call records",
    detail:
      "For each call: the caller’s number, time and duration, and the AI summary — including the caller’s name, callback number, intent and urgency as understood from the conversation. The conversation transcript received from our voice provider is stored so the summary can be produced; it is not shown in the dashboard.",
  },
  {
    term: "Usage records",
    detail: "Call minutes and related usage for each billing period, used to show usage against your plan.",
  },
  {
    term: "Sign-in activity",
    detail:
      "Your email address, your sessions, and the IP address and browser information associated with sign-in requests, used to keep your account secure.",
  },
] as const;

export default function PrivacyPage() {
  return (
    <>
      <InfoHero
        eyebrow="Privacy"
        title="Privacy at a glance"
        intro={`A plain-language summary of the information ${BRAND.name} stores to run your AI receptionist.`}
      />
      <InfoBody>
        <Alert tone="info" title="This is a summary, not our full privacy policy">
          The complete privacy policy will be published on this page. Until then, please contact us with
          any questions about how your information is handled.
        </Alert>

        <InfoSection id="what-we-store" title="What we store">
          <FactList items={STORED} />
        </InfoSection>

        <InfoSection id="how-we-use-it" title="How it’s used">
          <p>
            This information is used to answer calls for your business, write and deliver call summaries,
            show your dashboard, measure usage against your plan, and keep your account secure.
          </p>
          <p>
            Calls are handled with the help of telephone, voice and AI providers, which process call audio
            and content on our behalf so the receptionist can hold a conversation and summarize it.
          </p>
        </InfoSection>
      </InfoBody>
    </>
  );
}
