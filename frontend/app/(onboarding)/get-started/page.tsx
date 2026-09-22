import type { Metadata } from "next";
import { Suspense } from "react";

import { GetStartedGate } from "@/components/onboarding/GetStartedGate";

export const metadata: Metadata = { title: "Get started" };

export default function GetStartedPage() {
  // The gate reads `?another=1`, which needs a Suspense boundary for the route
  // to prerender.
  return (
    <Suspense fallback={null}>
      <GetStartedGate />
    </Suspense>
  );
}
