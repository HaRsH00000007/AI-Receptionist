import type { Metadata } from "next";

import { ReceptionistView } from "@/components/app/views/ReceptionistView";

export const metadata: Metadata = { title: "AI Receptionist" };

export default function ReceptionistPage() {
  return <ReceptionistView />;
}
