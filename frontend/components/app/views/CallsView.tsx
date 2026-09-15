"use client";

/**
 * Every call, searchable and filterable.
 *
 * The API returns calls newest first with no server-side filters, capped at
 * 100. Filtering therefore happens here, over that window, and the page says
 * so when the window is full rather than implying it searched everything.
 */

import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { CallDetailDrawer } from "@/components/app/CallDetailDrawer";
import { CallList } from "@/components/app/CallList";
import { CALL_FETCH_LIMIT, useTenantData } from "@/components/app/TenantWorkspace";
import { useNow } from "@/components/app/useNow";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import { SelectField, TextField } from "@/components/ui/Field";
import { ListIcon, RefreshIcon, SearchIcon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { humanize, pluralize } from "@/lib/format";
import { CALL_STATUS_FILTERS, callMatchesStatusFilter, isUrgent } from "@/lib/status";
import type { CallView } from "@/lib/types";

type DateRange = "" | "today" | "7d" | "30d";
type DurationRange = "" | "short" | "medium" | "long";

const DAY_MS = 86_400_000;

const DATE_OPTIONS = [
  { value: "", label: "Any time" },
  { value: "today", label: "Today" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
];

const DURATION_OPTIONS = [
  { value: "", label: "Any length" },
  { value: "short", label: "Under 1 minute" },
  { value: "medium", label: "1 to 5 minutes" },
  { value: "long", label: "Over 5 minutes" },
];

export function CallsView() {
  const params = useSearchParams();
  // Keyed on the query string, so following a notification link to
  // `?urgent=1` while already on this page resets the filters to match it.
  return (
    <CallsExplorer
      key={params.toString()}
      initialStatus={params.get("status") ?? ""}
      initialUrgent={params.get("urgent") === "1"}
    />
  );
}

function matchesQuery(call: CallView, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const text = [call.caller_name, call.summary, call.intent?.replace(/_/g, " ")]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (text.includes(needle)) return true;
  const digits = needle.replace(/\D/g, "");
  return (
    digits.length >= 3 &&
    [call.from_e164, call.callback_number].some((number) => number?.replace(/\D/g, "").includes(digits))
  );
}

function withinRange(call: CallView, range: DateRange, now: number): boolean {
  if (!range) return true;
  if (!call.started_at) return false;
  const time = new Date(call.started_at).getTime();
  if (range === "today") {
    const midnight = new Date(now);
    midnight.setHours(0, 0, 0, 0);
    return time >= midnight.getTime();
  }
  return time >= now - (range === "7d" ? 7 : 30) * DAY_MS;
}

function withinDuration(call: CallView, duration: DurationRange): boolean {
  if (!duration) return true;
  const seconds = call.duration_s;
  if (seconds == null) return false;
  if (duration === "short") return seconds < 60;
  if (duration === "medium") return seconds >= 60 && seconds <= 300;
  return seconds > 300;
}

function CallsExplorer({
  initialStatus,
  initialUrgent,
}: {
  initialStatus: string;
  initialUrgent: boolean;
}) {
  const { calls, refresh, refreshing } = useTenantData();
  const now = useNow();

  const [query, setQuery] = useState("");
  const [status, setStatus] = useState(initialStatus);
  const [intent, setIntent] = useState("");
  const [range, setRange] = useState<DateRange>("");
  const [duration, setDuration] = useState<DurationRange>("");
  const [urgentOnly, setUrgentOnly] = useState(initialUrgent);
  const [selected, setSelected] = useState<CallView | null>(null);

  const intentOptions = useMemo(() => {
    const intents = Array.from(
      new Set(calls.map((call) => call.intent).filter((value): value is string => Boolean(value))),
    ).sort();
    return [{ value: "", label: "Any intent" }, ...intents.map((value) => ({ value, label: humanize(value) }))];
  }, [calls]);

  const filtered = useMemo(
    () =>
      calls.filter(
        (call) =>
          matchesQuery(call, query) &&
          callMatchesStatusFilter(call.status, status) &&
          (!intent || call.intent === intent) &&
          withinRange(call, range, now) &&
          withinDuration(call, duration) &&
          (!urgentOnly || isUrgent(call.urgency)),
      ),
    [calls, query, status, intent, range, now, duration, urgentOnly],
  );

  const filtering = Boolean(query || status || intent || range || duration || urgentOnly);

  function clearFilters() {
    setQuery("");
    setStatus("");
    setIntent("");
    setRange("");
    setDuration("");
    setUrgentOnly(false);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Calls"
        description="Every conversation your receptionist has handled, each with an AI summary."
        actions={
          <Button
            variant="secondary"
            onClick={refresh}
            loading={refreshing}
            leadingIcon={<RefreshIcon size={16} />}
          >
            Refresh
          </Button>
        }
      />

      <Card className="p-4 sm:p-5">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[minmax(0,1.6fr)_repeat(4,minmax(0,1fr))]">
          <TextField
            label="Search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Caller, number or summary"
            className="sm:col-span-2 lg:col-span-1"
          />
          <SelectField
            label="Date"
            value={range}
            options={DATE_OPTIONS}
            onChange={(event) => setRange(event.target.value as DateRange)}
          />
          <SelectField
            label="Status"
            value={status}
            options={[{ value: "", label: "Any status" }, ...CALL_STATUS_FILTERS]}
            onChange={(event) => setStatus(event.target.value)}
          />
          <SelectField
            label="Intent"
            value={intent}
            options={intentOptions}
            onChange={(event) => setIntent(event.target.value)}
          />
          <SelectField
            label="Duration"
            value={duration}
            options={DURATION_OPTIONS}
            onChange={(event) => setDuration(event.target.value as DurationRange)}
          />
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
          <label className="inline-flex cursor-pointer items-center gap-2.5 text-sm font-medium text-ink-2">
            <input
              type="checkbox"
              className="size-4 accent-accent"
              checked={urgentOnly}
              onChange={(event) => setUrgentOnly(event.target.checked)}
            />
            Urgent calls only
          </label>
          {filtering && (
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              Clear filters
            </Button>
          )}
        </div>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader
          title={pluralize(filtered.length, "call")}
          description={
            filtering
              ? `Matching your filters, from your ${pluralize(calls.length, "most recent call", "most recent calls")}`
              : calls.length >= CALL_FETCH_LIMIT
                ? `Your ${CALL_FETCH_LIMIT} most recent calls, newest first`
                : "Newest first"
          }
        />
        {calls.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<ListIcon />}
              title="No calls yet"
              description="When your receptionist answers a call, it appears here with a summary."
            />
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<SearchIcon />}
              title="No calls match these filters"
              description="Try a wider date range or clear the filters."
              action={
                <Button variant="secondary" onClick={clearFilters}>
                  Clear filters
                </Button>
              }
            />
          </div>
        ) : (
          <CallList calls={filtered} onSelect={setSelected} now={now} />
        )}
      </Card>

      <CallDetailDrawer call={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
