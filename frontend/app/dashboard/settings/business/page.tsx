import type { Metadata } from "next";

import { BusinessSettings } from "@/components/app/settings/BusinessSettings";

export const metadata: Metadata = { title: "Business settings" };

export default function Page() {
  return <BusinessSettings />;
}
