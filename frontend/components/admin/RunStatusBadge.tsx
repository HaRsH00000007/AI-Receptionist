import { Badge } from "@/components/ui/Badge";
import { humanize } from "@/lib/format";
import { PROVISIONING_STATUS, type StatusMeta } from "@/lib/status";
import type { ProvisioningStatus } from "@/lib/types";

export function RunStatusBadge({ status }: { status: ProvisioningStatus }) {
  // A status added on the backend before this map learns it still renders honestly.
  const meta: StatusMeta = PROVISIONING_STATUS[status] ?? {
    label: humanize(status),
    tone: "neutral",
    description: "",
  };
  return (
    <span title={meta.description || undefined}>
      <Badge tone={meta.tone} dot pulse={meta.tone === "accent"}>
        {meta.label}
      </Badge>
    </span>
  );
}
