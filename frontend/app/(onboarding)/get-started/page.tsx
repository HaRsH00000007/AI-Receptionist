import type { Metadata } from "next";

import { SignupWizard } from "@/components/onboarding/SignupWizard";

export const metadata: Metadata = { title: "Get started" };

export default function GetStartedPage() {
  return <SignupWizard />;
}
