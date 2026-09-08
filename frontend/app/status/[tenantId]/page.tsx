import { StatusView } from "./StatusView";

/**
 * Next 15+ passes route params as a promise, so the page is async and the
 * client component receives a plain string.
 */
export default async function StatusPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  return <StatusView tenantId={tenantId} />;
}
