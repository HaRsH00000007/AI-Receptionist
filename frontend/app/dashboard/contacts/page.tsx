import type { Metadata } from "next";

import { ContactsView } from "@/components/app/views/ContactsView";

export const metadata: Metadata = { title: "Contacts" };

export default function Page() {
  return <ContactsView />;
}
