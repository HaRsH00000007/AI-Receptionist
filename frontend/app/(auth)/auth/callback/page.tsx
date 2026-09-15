import type { Metadata } from "next";
import { Suspense } from "react";

import { CallbackWorking, MagicLinkCallback } from "@/components/auth/MagicLinkCallback";

export const metadata: Metadata = {
  title: "Signing you in",
  // The address carries a single-use credential until the page strips it.
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default function MagicLinkCallbackPage() {
  return (
    <Suspense fallback={<CallbackWorking />}>
      <MagicLinkCallback />
    </Suspense>
  );
}
