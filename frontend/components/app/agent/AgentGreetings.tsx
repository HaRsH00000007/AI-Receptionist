"use client";

/**
 * Agents → General: the greetings that replace the usual one on a schedule.
 *
 * Two kinds, because businesses describe their exceptions in two ways and
 * collapsing them into one makes both awkward: *every weekday between noon and
 * one* is a weekly rule, *the 24th to the 26th of December* is a dated one. A
 * dated rule wins when both match, which is what a closure means.
 *
 * Three things this panel is careful about:
 *
 * **It says which line is in force.** A list of windows is not an answer to
 * "what do my callers hear right now", so the panel answers that first and
 * leaves the list underneath as the explanation.
 *
 * **It names the clock.** Every rule is read in the business's own timezone —
 * the one the opening hours are stored in, set further up this same page — so
 * the panel prints it rather than letting someone assume their own.
 *
 * **A saved rule is not necessarily an audible one.** Saving republishes and
 * pushes the greeting to the voice agent; when that push fails the API says so
 * and this page keeps saying it, because until it syncs callers are still
 * hearing the previous line.
 */

import { useState, type FormEvent } from "react";

import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { useLoad } from "@/components/app/useLoad";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { Skeleton } from "@/components/ui/Feedback";
import { TextAreaField, TextField } from "@/components/ui/Field";
import { CalendarIcon, ClockIcon, PlusIcon } from "@/components/ui/icons";
import { Menu } from "@/components/ui/Menu";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  createGreetingRule,
  deleteGreetingRule,
  listGreetingRules,
  updateGreetingRule,
} from "@/lib/api";
import {
  GREETING_MAX_LENGTH,
  WEEKDAY_NAMES,
  formatDateRange,
  formatMinuteOfDay,
  formatWeekdays,
  minutesToTimeValue,
  timeValueToMinutes,
} from "@/lib/greetings";
import type { GreetingRuleKind, GreetingRuleResult, GreetingRuleView } from "@/lib/types";

/** Noon to one: the default a weekly rule opens on, because it is the case. */
const DEFAULT_START = "12:00";
const DEFAULT_END = "13:00";

/** Values the dialog collects, in the shapes its inputs use. */
interface RuleValues {
  message: string;
  weekdays: number[];
  start: string;
  end: string;
  startDate: string;
  endDate: string;
  timed: boolean;
}

export function AgentGreetings() {
  const { tenantId } = useTenantData();
  const canManage = useCanManage();
  const toast = useToast();

  const listing = useLoad(() => listGreetingRules(tenantId), `greeting-rules:${tenantId}`);
  const { data, error, loading } = listing;

  const [editing, setEditing] = useState<GreetingRuleView | null>(null);
  const [adding, setAdding] = useState<GreetingRuleKind | null>(null);
  const [removing, setRemoving] = useState<GreetingRuleView | null>(null);
  const [busy, setBusy] = useState(false);
  // Kept on the page, not only in a toast: when the push to the voice agent
  // fails, "callers still hear the previous greeting" has to stay on screen.
  const [notice, setNotice] = useState<{ tone: "success" | "warn"; text: string } | null>(null);

  const rules = data?.rules ?? [];
  const weekly = rules.filter((rule) => rule.kind === "weekly");
  const dated = rules.filter((rule) => rule.kind === "dated");
  const atLimit = data ? rules.length >= data.max_rules : false;
  const scheduled = data ? data.active_message !== data.default_greeting : false;

  function fail(caught: unknown, fallback: string) {
    const failure = caught instanceof ApiError ? caught : null;
    toast({ tone: "danger", title: fallback, description: failure?.message });
  }

  /** Fold a write's answer back into the page: the list, and what it did. */
  function applyResult(result: GreetingRuleResult, title: string) {
    listing.set(result);
    setNotice({ tone: result.synced ? "success" : "warn", text: result.detail });
    toast({
      tone: result.synced ? "success" : "info",
      title,
      description: result.detail,
    });
  }

  async function save(values: RuleValues): Promise<boolean> {
    const kind = editing ? editing.kind : adding;
    if (!kind) return false;
    setBusy(true);
    try {
      const window = values.timed
        ? {
            start_minute: timeValueToMinutes(values.start),
            end_minute: timeValueToMinutes(values.end),
          }
        : { start_minute: null, end_minute: null };
      const result = editing
        ? await updateGreetingRule(tenantId, editing.id, {
            message: values.message,
            ...(kind === "weekly"
              ? { weekdays: values.weekdays, ...window }
              : {
                  start_date: values.startDate,
                  end_date: values.endDate || values.startDate,
                  ...window,
                }),
          })
        : await createGreetingRule(tenantId, {
            kind,
            message: values.message,
            ...(kind === "weekly"
              ? { weekdays: values.weekdays, ...window }
              : {
                  start_date: values.startDate,
                  end_date: values.endDate || values.startDate,
                  ...window,
                }),
          });
      applyResult(result, editing ? "Greeting updated" : "Greeting added");
      setEditing(null);
      setAdding(null);
      return true;
    } catch (caught) {
      fail(caught, editing ? "Couldn't update that greeting" : "Couldn't add that greeting");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function toggle(rule: GreetingRuleView) {
    try {
      const result = await updateGreetingRule(tenantId, rule.id, { enabled: !rule.enabled });
      applyResult(result, rule.enabled ? "Greeting switched off" : "Greeting switched on");
    } catch (caught) {
      fail(caught, "Couldn't change that greeting");
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      const result = await deleteGreetingRule(tenantId, removing.id);
      applyResult(result, "Greeting removed");
      setRemoving(null);
    } catch (caught) {
      fail(caught, "Couldn't remove that greeting");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) {
    return (
      <Card>
        <div className="space-y-2 p-5">
          <Skeleton className="h-10 rounded-lg" />
          <Skeleton className="h-10 rounded-lg" />
        </div>
      </Card>
    );
  }

  if (error) {
    return (
      <Alert
        tone="danger"
        title="Your scheduled greetings couldn't be loaded"
        action={
          <Button size="sm" variant="secondary" onClick={() => listing.reload()}>
            Try again
          </Button>
        }
      >
        {error}
      </Alert>
    );
  }

  function addButton(kind: GreetingRuleKind) {
    if (!canManage) return undefined;
    return (
      <Button
        size="sm"
        variant="secondary"
        leadingIcon={<PlusIcon size={15} />}
        disabled={atLimit}
        onClick={() => {
          setNotice(null);
          setAdding(kind);
        }}
      >
        Add rule
      </Button>
    );
  }

  const rows = (list: GreetingRuleView[], empty: string) =>
    list.length === 0 ? (
      <p className="px-4 pb-4 text-sm text-muted">{empty}</p>
    ) : (
      <ul className="divide-y divide-line border-t border-line">
        {list.map((rule) => (
          <li key={rule.id} className="flex items-start gap-3 p-4">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-ink">{describe(rule)}</span>
                {!rule.enabled && <Badge tone="warn">Off</Badge>}
              </div>
              <p className="mt-1 text-sm text-muted">“{rule.message}”</p>
            </div>
            {canManage ? (
              <Menu
                label={`Actions for the greeting on ${describe(rule)}`}
                iconOnly
                trigger="⋯"
                width="w-56"
                items={[
                  {
                    label: "Edit greeting",
                    onSelect: () => {
                      setNotice(null);
                      setEditing(rule);
                    },
                  },
                  {
                    label: rule.enabled ? "Switch off" : "Switch on",
                    description: rule.enabled
                      ? "Keeps the wording, stops it being said."
                      : undefined,
                    onSelect: () => void toggle(rule),
                  },
                  { label: "Delete greeting", tone: "danger", onSelect: () => setRemoving(rule) },
                ]}
              />
            ) : null}
          </li>
        ))}
      </ul>
    );

  return (
    <div className="space-y-6">
      {notice ? <Alert tone={notice.tone}>{notice.text}</Alert> : null}

      {scheduled && data?.active_message ? (
        /*
         * Only while an exception is running. With no rule in force this would
         * repeat the greeting field directly above it, and a banner that is
         * always there is a banner nobody reads on the day it matters.
         */
        <Card>
          <div className="space-y-1 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-semibold text-ink">Right now, callers hear</p>
              <Badge tone="accent">Scheduled greeting</Badge>
            </div>
            <p className="text-sm text-muted">“{data.active_message}”</p>
            <p className="text-xs text-subtle">
              Your usual greeting comes back on its own when this rule&apos;s hours are over.
            </p>
          </div>
        </Card>
      ) : null}

      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              <ClockIcon size={18} className="text-muted" />
              Different greeting at certain times
            </span>
          }
          description="Use this for things that repeat weekly, like lunch hours."
          action={addButton("weekly")}
        />
        <Zone timezone={data?.timezone} />
        {rows(weekly, "No weekly greetings. Your usual greeting is used at every hour.")}
      </Card>

      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              <CalendarIcon size={18} className="text-muted" />
              Different greeting on specific dates
            </span>
          }
          description="Use this for one-off dates, like holidays or office closures."
          action={addButton("dated")}
        />
        <Zone timezone={data?.timezone} />
        {rows(dated, "No dates set. Your usual greeting is used every day of the year.")}
      </Card>

      {atLimit ? (
        <Alert tone="info">
          That&apos;s the most scheduled greetings a receptionist can have. Delete one to add
          another.
        </Alert>
      ) : null}

      {adding || editing ? (
        <RuleDialog
          kind={editing ? editing.kind : adding!}
          rule={editing}
          busy={busy}
          onClose={() => {
            setAdding(null);
            setEditing(null);
          }}
          onSave={save}
        />
      ) : null}

      <ConfirmDialog
        open={removing !== null}
        title="Delete this greeting?"
        confirmLabel="Delete greeting"
        busy={busy}
        onCancel={() => setRemoving(null)}
        onConfirm={() => void remove()}
      >
        Callers will hear your usual greeting at those times instead. You can add it back at any
        time.
      </ConfirmDialog>
    </div>
  );
}

/**
 * The clock every rule in this card is read against.
 *
 * Shown, not editable: it is the tenant's timezone, changed once further up
 * this page, and a second control for it here would be a second way for the
 * opening hours and the greetings to disagree about what time it is.
 */
function Zone({ timezone }: { timezone: string | undefined }) {
  if (!timezone) return null;
  return (
    <div className="px-4 pb-4 text-sm">
      <span className="field-label">Time zone</span>
      <p className="text-muted">{timezone.replace(/_/g, " ")}</p>
    </div>
  );
}

/** "Mon–Fri, 12:00 PM to 1:00 PM", or "25 Dec 2026". */
function describe(rule: GreetingRuleView): string {
  const window =
    rule.start_minute === null || rule.end_minute === null
      ? "all day"
      : `${formatMinuteOfDay(rule.start_minute)} to ${formatMinuteOfDay(rule.end_minute)}`;
  if (rule.kind === "weekly") return `${formatWeekdays(rule.weekdays)}, ${window}`;
  const dates = formatDateRange(rule.start_date, rule.end_date);
  return rule.start_minute === null ? dates : `${dates}, ${window}`;
}

// ---------------------------------------------------------------------------

function RuleDialog({
  kind,
  rule,
  busy,
  onClose,
  onSave,
}: {
  kind: GreetingRuleKind;
  rule: GreetingRuleView | null;
  busy: boolean;
  onClose: () => void;
  onSave: (values: RuleValues) => Promise<boolean>;
}) {
  const weekly = kind === "weekly";
  const [message, setMessage] = useState(rule?.message ?? "");
  const [weekdays, setWeekdays] = useState<number[]>(rule?.weekdays ?? [0, 1, 2, 3, 4]);
  const [start, setStart] = useState(
    rule?.start_minute != null ? minutesToTimeValue(rule.start_minute) : DEFAULT_START,
  );
  const [end, setEnd] = useState(
    rule?.end_minute != null ? minutesToTimeValue(rule.end_minute) : DEFAULT_END,
  );
  const [startDate, setStartDate] = useState(rule?.start_date ?? "");
  const [endDate, setEndDate] = useState(rule?.end_date ?? "");
  // A weekly rule always has a window; a dated one is all day unless asked.
  const [timed, setTimed] = useState(weekly || rule?.start_minute != null);
  const [problem, setProblem] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    // Checked here only to save a round trip; the API is the authority on all
    // of it, and refuses the same things with the same words.
    if (!message.trim()) {
      setProblem("Write what your receptionist should say.");
      return;
    }
    if (timed && (timeValueToMinutes(start) === null || timeValueToMinutes(end) === null)) {
      setProblem("Enter a start and end time.");
      return;
    }
    if (!weekly && !startDate) {
      setProblem("Choose the date this greeting applies to.");
      return;
    }
    if (!weekly && endDate && endDate < startDate) {
      setProblem("The last date can't be before the first one.");
      return;
    }
    setProblem(null);
    await onSave({
      message: message.trim(),
      weekdays,
      start,
      end,
      startDate,
      endDate,
      timed,
    });
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={
        rule ? "Edit greeting" : weekly ? "Greeting at certain times" : "Greeting on specific dates"
      }
      description="Callers hear this instead of your usual greeting, word for word."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button form="greeting-rule-form" type="submit" loading={busy}>
            {rule ? "Save changes" : "Add rule"}
          </Button>
        </>
      }
    >
      <form id="greeting-rule-form" className="space-y-4" noValidate onSubmit={submit}>
        {problem && <Alert tone="danger">{problem}</Alert>}
        <TextAreaField
          label="Greeting message"
          rows={3}
          maxLength={GREETING_MAX_LENGTH}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Thanks for calling. We're closed for lunch until one — leave a message and we'll call you back."
          data-autofocus
        />

        {weekly ? (
          <fieldset>
            <legend className="field-label">Days</legend>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {WEEKDAY_NAMES.map((name, day) => {
                const on = weekdays.includes(day);
                return (
                  <label
                    key={name}
                    className={`cursor-pointer rounded-lg border px-3 py-1.5 text-sm ${
                      on ? "border-accent bg-accent-soft text-ink" : "border-line text-muted"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={on}
                      onChange={(event) =>
                        setWeekdays((current) =>
                          event.target.checked
                            ? [...current, day].sort((a, b) => a - b)
                            : current.filter((value) => value !== day),
                        )
                      }
                    />
                    {name}
                  </label>
                );
              })}
            </div>
            <p className="field-hint">
              Leave every day selected for a greeting that applies daily.
            </p>
          </fieldset>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="First date"
              type="date"
              value={startDate}
              onChange={(event) => setStartDate(event.target.value)}
            />
            <TextField
              label="Last date"
              optional
              type="date"
              value={endDate}
              onChange={(event) => setEndDate(event.target.value)}
              hint="Leave blank for a single day."
            />
          </div>
        )}

        {weekly ? null : (
          <label className="flex cursor-pointer gap-3 rounded-xl border border-line p-3.5 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 size-4 shrink-0 accent-accent"
              checked={timed}
              onChange={(event) => setTimed(event.target.checked)}
            />
            <span className="text-muted">
              Only at certain times of day. Otherwise it applies for the whole of those dates.
            </span>
          </label>
        )}

        {timed ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="From"
              type="time"
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
            <TextField
              label="To"
              type="time"
              value={end}
              onChange={(event) => setEnd(event.target.value)}
              hint="An end before the start runs overnight, into the next morning."
            />
          </div>
        ) : null}
      </form>
    </Dialog>
  );
}
