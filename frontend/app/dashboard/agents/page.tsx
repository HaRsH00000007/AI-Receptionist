import type { Metadata } from "next";

import { ReceptionistsView } from "@/components/app/views/ReceptionistsView";

export const metadata: Metadata = { title: "Receptionists" };

export default function Page() {
  return <ReceptionistsView />;
}
