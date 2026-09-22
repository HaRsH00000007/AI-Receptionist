import type { Metadata } from "next";

import { SmsView } from "@/components/app/views/SmsView";

export const metadata: Metadata = { title: "SMS Campaigns" };

export default function Page() {
  return <SmsView />;
}
