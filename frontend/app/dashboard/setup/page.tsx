import type { Metadata } from "next";

import { SetupView } from "@/components/app/views/SetupView";

export const metadata: Metadata = { title: "Setting up" };

export default function Page() {
  return <SetupView />;
}
