import type { Metadata } from "next";

import { StatusView } from "@/components/onboarding/StatusView";

export const metadata: Metadata = {
  title: "Setting up your receptionist",
  // The address carries a signed status grant; it must not leave in a Referer.
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

/**
 * Next 15+ passes route params and search params as promises, so the page is
 * async and the client component receives plain strings.
 *
 * The status grant travels in the query string rather than the path: it is a
 * credential, and keeping it out of the route keeps it out of the path segment
 * that gets logged, bookmarked and pasted into support tickets as an identifier.
 */
export default async function StatusPage({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ t?: string; setup?: string }>;
}) {
  const { tenantId } = await params;
  const { t, setup } = await searchParams;
  return <StatusView tenantId={tenantId} statusToken={t} forwarding={setup === "forward"} />;
}
