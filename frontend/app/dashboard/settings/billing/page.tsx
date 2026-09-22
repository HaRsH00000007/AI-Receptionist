import type { Metadata } from "next";

import { BillingView } from "@/components/app/views/BillingView";
import { UsageView } from "@/components/app/views/UsageView";

export const metadata: Metadata = { title: "Billing settings" };

export default function Page() {
  return (
    <div className="space-y-10">
      <BillingView embedded />
      <section aria-labelledby="usage-heading" className="space-y-4">
        <h2 id="usage-heading" className="text-lg font-bold tracking-tight">
          Usage this period
        </h2>
        <UsageView embedded />
      </section>
    </div>
  );
}
