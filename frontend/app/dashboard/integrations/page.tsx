import type { Metadata } from "next";
import { Suspense } from "react";

import { IntegrationsView } from "@/components/app/views/IntegrationsView";

export const metadata: Metadata = { title: "Integrations" };

export default function Page() {
  // The view reads the query string, which needs a Suspense boundary for the
  // route to prerender.
  return (
    <Suspense fallback={null}>
      <IntegrationsView />
    </Suspense>
  );
}
