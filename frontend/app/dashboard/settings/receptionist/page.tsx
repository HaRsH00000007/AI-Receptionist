import type { Metadata } from "next";

import { ReceptionistSettings } from "@/components/app/settings/ReceptionistSettings";

export const metadata: Metadata = { title: "Receptionist settings" };

export default function Page() {
  return <ReceptionistSettings />;
}
