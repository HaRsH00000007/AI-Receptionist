import type { Metadata } from "next";

import { ConfigurationView } from "@/components/app/views/ConfigurationView";

export const metadata: Metadata = { title: "Configuration" };

export default function ConfigurationPage() {
  return <ConfigurationView />;
}
