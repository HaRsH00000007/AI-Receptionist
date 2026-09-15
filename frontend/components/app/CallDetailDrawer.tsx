"use client";

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { CopyButton } from "@/components/ui/CopyButton";
import { Dialog } from "@/components/ui/Dialog";
import { InfoIcon, PhoneIcon } from "@/components/ui/icons";
import { formatDateTime, formatDuration, formatPhone, humanize } from "@/lib/format";
import { callStatusMeta, isUrgent } from "@/lib/status";
import type { CallView } from "@/lib/types";

import { callerName } from "./CallList";

export function CallDetailDrawer({ call, onClose }: { call: CallView | null; onClose: () => void }) {
  return (
    <Dialog
      open={call !== null}
      onClose={onClose}
      variant="drawer"
      title={call ? callerName(call) : "Call"}
      description={call ? formatDateTime(call.started_at) : undefined}
    >
      {call && <CallDetail call={call} />}
    </Dialog>
  );
}

function summaryFallback(status: string): string {
  if (status === "received" || status === "transcribed") {
    return "The summary is still being written. It usually appears shortly after the call ends.";
  }
  if (status === "failed") return "This call couldn't be summarized automatically.";
  return "No summary was recorded for this call.";
}

function CallDetail({ call }: { call: CallView }) {
  const status = callStatusMeta(call.status);
  const urgent = isUrgent(call.urgency);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2">
        <Badge tone={status.tone} dot>
          {status.label}
        </Badge>
        {urgent && <Badge tone="warn">Urgent</Badge>}
        {call.intent && <Badge tone="accent">{humanize(call.intent)}</Badge>}
      </div>

      <section>
        <h3 className="text-xs font-semibold tracking-[0.06em] text-muted uppercase">Summary</h3>
        <p className="mt-2 text-[0.9375rem] leading-relaxed text-ink">
          {call.summary ?? summaryFallback(call.status)}
        </p>
      </section>

      <dl className="grid grid-cols-1 gap-4 rounded-xl border border-line bg-surface-2 p-4 text-sm sm:grid-cols-2">
        <Detail label="Caller">{call.caller_name ?? "Not given"}</Detail>
        <Detail label="Caller ID">
          <span className="tabular-nums">{formatPhone(call.from_e164)}</span>
        </Detail>
        <Detail label="Callback number">
          {call.callback_number ? (
            <>
              <span className="tabular-nums">{formatPhone(call.callback_number)}</span>
              {/* Read off the conversation by a model: the caller claimed it, and
                  nothing verified it. It is shown, never offered as a dial link. */}
              <span className="mt-0.5 block text-xs text-muted">As stated by the caller</span>
            </>
          ) : (
            "Not given"
          )}
        </Detail>
        <Detail label="Time">{formatDateTime(call.started_at)}</Detail>
        <Detail label="Duration">{formatDuration(call.duration_s)}</Detail>
        <Detail label="Urgency">{call.urgency !== null ? `${call.urgency} of 5` : "—"}</Detail>
        <Detail label="Intent">{humanize(call.intent)}</Detail>
        <Detail label="Direction">{humanize(call.direction)}</Detail>
      </dl>

      {call.from_e164 && (
        <div className="flex flex-wrap gap-2.5">
          <ButtonLink href={`tel:${call.from_e164}`} leadingIcon={<PhoneIcon size={16} />}>
            Call back
          </ButtonLink>
          <CopyButton value={call.from_e164} label="Copy caller ID" size="md" />
        </div>
      )}

      <div className="flex gap-2.5 rounded-xl border border-line px-4 py-3 text-sm text-muted">
        <InfoIcon size={16} className="mt-0.5 shrink-0" />
        <p>
          The dashboard shows the AI summary of each call. Transcripts and recordings aren&apos;t
          displayed.
        </p>
      </div>

      <p className="text-xs text-subtle">
        Reference <span className="font-mono break-all">{call.provider_call_id}</span>
      </p>
    </div>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 font-medium break-words text-ink">{children}</dd>
    </div>
  );
}
