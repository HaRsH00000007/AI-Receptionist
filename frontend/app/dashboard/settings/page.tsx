import type { Metadata } from "next";

import { AccountSettings } from "@/components/app/settings/AccountSettings";

export const metadata: Metadata = { title: "Settings" };

export default function Page() {
  return <AccountSettings />;
}
