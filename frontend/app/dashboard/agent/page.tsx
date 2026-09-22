import type { Metadata } from "next";

import { AgentView } from "@/components/app/views/AgentView";

export const metadata: Metadata = { title: "Agent" };

export default function Page() {
  return <AgentView />;
}
