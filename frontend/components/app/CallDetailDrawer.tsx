"use client";

/**
 * One call, opened from a list.
 *
 * The row the list already has is shown at once; the full call — transcript,
 * the configuration version that answered — is fetched when the drawer opens,
 * so the lists stay light and a transcript is only transferred when someone
 * asks to read it.
 */

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { CopyButton } from "@/components/ui/CopyButton";
import { cx } from "@/components/ui/cx";
import { Dialog } from "@/components/ui/Dialog";
import { Skeleton } from "@/components/ui/Feedback";
import { InfoIcon, PhoneIcon } from "@/components/ui/icons";
import { getCallDetail } from "@/lib/api";
import { formatDateTime, formatDuration, formatPhone, humanize } from "@/lib/format";
import { callStatusMeta, isUrgent } from "@/lib/status";
import type { CallView, TranscriptTurnView } from "@/lib/types";

import { callerName } from "./CallList";
import { useWorkspace } from "./TenantWorkspace";
import { useLoad } from "./useLoad";

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
  const { tenantId } = useWorkspace();
  const detail = useLoad(() => getCallDetail(tenantId, call.id), `call:${tenantId}:${call.id}`);
  const status = callStatusMeta(call.status);
  const urgent = isUrgent(call.urgency);
  const full = detail.data;

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
        <Detail label="Direction">{humanize(call.direction)}</Detail>
        <Detail label="Outcome">{status.description || status.label}</Detail>
        <Detail label="Urgency">{call.urgency !== null ? `${call.urgency} of 5` : "—"}</Detail>
        <Detail label="Intent">{humanize(call.intent)}</Detail>
        <Detail label="Transferred">Not tracked yet</Detail>
        <Detail label="Answered by">
          {full?.agent_config_version ? `Configuration v${full.agent_config_version}` : "—"}
        </Detail>
        <Detail label="Recording">
          {full?.recording_available ? "Available" : "Not recorded"}
        </Detail>
      </dl>

      {call.from_e164 && (
        <div className="flex flex-wrap gap-2.5">
          <ButtonLink href={`tel:${call.from_e164}`} leadingIcon={<PhoneIcon size={16} />}>
            Call back
          </ButtonLink>
          <CopyButton value={call.from_e164} label="Copy caller ID" size="md" />
        </div>
      )}

      <section aria-labelledby="transcript-heading">
        <h3
          id="transcript-heading"
          className="text-xs font-semibold tracking-[0.06em] text-muted uppercase"
        >
          Transcript
        </h3>
        <div className="mt-3">
          {detail.loading && !full ? (
            <div aria-busy="true" className="space-y-2">
              <Skeleton className="h-10 w-3/4 rounded-xl" />
              <Skeleton className="ml-auto h-10 w-2/3 rounded-xl" />
              <Skeleton className="h-10 w-1/2 rounded-xl" />
            </div>
          ) : detail.error ? (
            <p className="text-sm text-muted">The transcript couldn&apos;t be loaded. {detail.error}</p>
          ) : full && full.transcript.length > 0 ? (
            <Transcript turns={full.transcript} />
          ) : (
            <p className="text-sm text-muted">No transcript was recorded for this call.</p>
          )}
        </div>
      </section>

      <div className="flex gap-2.5 rounded-xl border border-line px-4 py-3 text-sm text-muted">
        <InfoIcon size={16} className="mt-0.5 shrink-0" />
        <p>
          The transcript is the voice agent&apos;s record of the conversation. Recordings
          aren&apos;t captured yet.
        </p>
      </div>

      <p className="text-xs text-subtle">
        Reference <span className="font-mono break-all">{call.provider_call_id}</span>
      </p>
    </div>
  );
}

function Transcript({ turns }: { turns: TranscriptTurnView[] }) {
  return (
    <ol className="space-y-2.5">
      {turns.map((turn, index) => {
        const agent = turn.role === "agent";
        return (
          <li key={index} className={cx("flex", agent ? "justify-start" : "justify-end")}>
            <div
              className={cx(
                "max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
                agent ? "rounded-bl-md bg-surface-2 text-ink" : "rounded-br-md bg-accent-soft text-accent-ink",
              )}
            >
              <p className="text-[0.6875rem] font-semibold tracking-wide uppercase opacity-70">
                {agent ? "Receptionist" : "Caller"}
                {turn.time_in_call_s !== null && ` · ${formatDuration(Math.round(turn.time_in_call_s))}`}
              </p>
              <p className="mt-0.5 whitespace-pre-wrap">{turn.message}</p>
            </div>
          </li>
        );
      })}
    </ol>
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
