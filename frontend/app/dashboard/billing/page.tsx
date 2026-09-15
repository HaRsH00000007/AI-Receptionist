import type { Metadata } from "next";

import { BillingView } from "@/components/app/views/BillingView";

export const metadata: Metadata = { title: "Billing" };

export default function BillingPage() {
  return <BillingView />;
}
