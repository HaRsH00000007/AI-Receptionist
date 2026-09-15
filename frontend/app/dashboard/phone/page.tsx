import type { Metadata } from "next";

import { PhoneView } from "@/components/app/views/PhoneView";

export const metadata: Metadata = { title: "Phone Numbers" };

export default function PhonePage() {
  return <PhoneView />;
}
