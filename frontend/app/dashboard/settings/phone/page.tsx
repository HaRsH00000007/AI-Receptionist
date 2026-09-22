import type { Metadata } from "next";

import { PhoneView } from "@/components/app/views/PhoneView";

export const metadata: Metadata = { title: "Phone settings" };

export default function Page() {
  return <PhoneView embedded />;
}
