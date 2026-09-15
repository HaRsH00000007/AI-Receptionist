import type { Metadata } from "next";

import { CallSummaryShowcase } from "@/components/marketing/CallSummaryShowcase";
import { Capabilities } from "@/components/marketing/Capabilities";
import { Features } from "@/components/marketing/Features";
import { FinalCta } from "@/components/marketing/FinalCta";
import { Hero } from "@/components/marketing/Hero";
import { HowItWorks } from "@/components/marketing/HowItWorks";
import { Industries } from "@/components/marketing/Industries";
import { LiveCallSection } from "@/components/marketing/LiveCallSection";
import { Pricing } from "@/components/marketing/Pricing";
import { ValueStrip } from "@/components/marketing/ValueStrip";
import { BRAND } from "@/lib/brand";

export const metadata: Metadata = {
  title: { absolute: `${BRAND.name} · AI receptionist for your business calls` },
  description: BRAND.description,
};

export default function HomePage() {
  return (
    <>
      <Hero />
      <ValueStrip />
      <Capabilities />
      <LiveCallSection />
      <HowItWorks />
      <Industries />
      <Features />
      <CallSummaryShowcase />
      <Pricing />
      <FinalCta />
    </>
  );
}
