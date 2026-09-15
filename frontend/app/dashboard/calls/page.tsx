import type { Metadata } from "next";
import { Suspense } from "react";

import { CallsView } from "@/components/app/views/CallsView";

export const metadata: Metadata = { title: "Calls" };

export default function CallsPage() {
  // The view reads filters from the query string, which needs a Suspense
  // boundary for the route to prerender.
  return (
    <Suspense fallback={null}>
      <CallsView />
    </Suspense>
  );
}
