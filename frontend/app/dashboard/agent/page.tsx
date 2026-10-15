import type { Metadata } from "next";
import { Suspense } from "react";

import { AgentSections } from "@/components/app/agent/AgentSections";

export const metadata: Metadata = { title: "Agent" };

export default function Page() {
  // The section list is addressed by `?section=`, which needs a Suspense
  // boundary for the route to prerender.
  return (
    <Suspense fallback={null}>
      <AgentSections />
    </Suspense>
  );
}
