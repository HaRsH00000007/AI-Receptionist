import type { Metadata } from "next";

import { NotificationSettings } from "@/components/app/settings/NotificationSettings";

export const metadata: Metadata = { title: "Notification settings" };

export default function Page() {
  return <NotificationSettings />;
}
