"use client";

/**
 * Integrations, chosen for the business's vertical.
 *
 * The list comes from the API, which filters the catalog by business type — a
 * law firm is offered Clio, a salon Acuity, neither the other's — so this page
 * renders whatever it is given and never decides that itself. Nothing here is
 * written around a particular provider.
 *
 * Three states are kept distinct on every card, because conflating them is how
 * a page ends up claiming something works when it does not:
 *
 * - *availability* — can this server connect it at all ("coming soon",
 *   "configuration required", or available);
 * - *status* — is this business connected, and is the connection healthy;
 * - *role* — may this viewer change it.
 *
 * "Connected" is only ever shown when the API says so, which it only does after
 * a real OAuth exchange.
 *
 * A connected calendar is not yet a calendar the receptionist books into: that
 * needs the owner to say *which* calendar, which is what the picker below is
 * for. Until they do, the card says so, because the alternative — guessing the
 * account's default — writes appointments somewhere nobody chose and nothing
 * reports it.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ComponentType } from "react";

import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { useLoad } from "@/components/app/useLoad";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cx } from "@/components/ui/cx";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Skeleton } from "@/components/ui/Feedback";
import {
  CalendarIcon,
  CheckIcon,
  ClockIcon,
  PlugIcon,
  ScaleIcon,
  ZapIcon,
  type IconProps,
} from "@/components/ui/icons";
import { SelectField } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  chooseCalendar,
  connectIntegration,
  disconnectIntegration,
  listCalendars,
  listIntegrations,
  verifyIntegration,
} from "@/lib/api";
import { formatDate, pluralize } from "@/lib/format";
import {
  businessTypeLabel,
  type CalendarOption,
  type IntegrationView,
} from "@/lib/types";

/** Our own icons, keyed by the catalog's `icon`. Never a vendor's logo. */
const ICONS: Record<string, ComponentType<IconProps>> = {
  "calendar-google": CalendarIcon,
  "calendar-outlook": CalendarIcon,
  scale: ScaleIcon,
  clock: ClockIcon,
  zap: ZapIcon,
};

const CATEGORY_LABELS: Record<string, string> = {
  practice_management: "Practice management",
  scheduling: "Scheduling",
  calendar: "Calendars",
  automation: "Automation",
};

const CAPABILITY_LABELS: Record<string, string> = {
  read_availability: "Check availability",
  read_calendar: "Read your calendar",
  create_event: "Book appointments",
  update_event: "Reschedule",
  cancel_event: "Cancel",
  book_appointment: "Book appointments",
  reschedule_appointment: "Reschedule",
  cancel_appointment: "Cancel",
  forward_call_events: "Send call summaries",
};

/** The callback's short codes, as sentences. */
const RETURN_ERRORS: Record<string, string> = {
  access_denied: "The connection was cancelled on the consent screen. Nothing was connected.",
  provider_error: "The provider refused the connection. Please try again.",
  expired_link: "That connection link expired. Start the connection again.",
  signed_out: "You were signed out during the connection. Sign in and try again.",
  not_permitted: "The connection has to be finished by the person who started it, signed in as an owner or admin.",
  exchange_failed: "We couldn't finish connecting. Please try again.",
  no_refresh_token:
    "The provider didn't grant ongoing access, so the connection would stop working within the hour. Please connect again and allow access.",
  invalid_request: "The provider sent back an incomplete response. Please try again.",
  unknown_integration: "That integration isn't available.",
};

export function IntegrationsView() {
  const { tenantId, tenant } = useTenantData();
  const canManage = useCanManage();
  const toast = useToast();
  const router = useRouter();
  const params = useSearchParams();
  const catalog = useLoad(() => listIntegrations(tenantId), `integrations:${tenantId}`);

  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<IntegrationView | null>(null);

  // The OAuth callback lands here with a short result code. Announce it once,
  // then drop it from the address bar so a reload does not repeat it.
  const announced = useRef(false);
  useEffect(() => {
    if (announced.current || !params) return;
    const connected = params.get("connected");
    const failed = params.get("integration_error");
    if (!connected && !failed) return;
    announced.current = true;
    if (connected) {
      toast({
        tone: "success",
        title: "Connected",
        description: "Your receptionist can now check this calendar and book appointments into it.",
      });
    } else if (failed) {
      toast({
        tone: "danger",
        title: "Not connected",
        description: RETURN_ERRORS[failed] ?? "Something went wrong. Please try again.",
      });
    }
    router.replace("/dashboard/integrations");
  }, [params, router, toast]);

  const groups = useMemo(() => {
    const byCategory = new Map<string, IntegrationView[]>();
    for (const entry of catalog.data?.integrations ?? []) {
      byCategory.set(entry.category, [...(byCategory.get(entry.category) ?? []), entry]);
    }
    return Array.from(byCategory.entries());
  }, [catalog.data]);

  async function connect(entry: IntegrationView) {
    setBusy(entry.id);
    try {
      const { authorization_url } = await connectIntegration(tenantId, entry.id);
      // A full navigation: the consent screen is the provider's page.
      window.location.assign(authorization_url);
    } catch (caught) {
      setBusy(null);
      toast({
        tone: "danger",
        title: `Couldn't connect ${entry.name}`,
        description: caught instanceof ApiError ? caught.message : "Please try again.",
      });
    }
  }

  async function verify(entry: IntegrationView) {
    setBusy(entry.id);
    try {
      const result = await verifyIntegration(tenantId, entry.id);
      toast({
        tone: "success",
        title: `${entry.name} is working`,
        description: `Your receptionist can see your calendar: ${pluralize(result.busy_blocks, "busy block")} in the next 7 days. It books callers into the free time.`,
      });
    } catch (caught) {
      toast({
        tone: "danger",
        title: `${entry.name} needs attention`,
        description: caught instanceof ApiError ? caught.message : "Please try again.",
      });
      catalog.reload();
    } finally {
      setBusy(null);
    }
  }

  async function disconnect(entry: IntegrationView) {
    setBusy(entry.id);
    try {
      await disconnectIntegration(tenantId, entry.id);
      toast({ tone: "success", title: `${entry.name} disconnected`, description: "Its access was removed." });
      catalog.reload();
    } catch (caught) {
      toast({
        tone: "danger",
        title: `Couldn't disconnect ${entry.name}`,
        description: caught instanceof ApiError ? caught.message : "Please try again.",
      });
    } finally {
      setBusy(null);
      setConfirming(null);
    }
  }

  return (
    <div className="space-y-6 lg:space-y-8">
      <PageHeader
        title="Integrations"
        description={`Connect the tools your ${businessTypeLabel(tenant.business_type).toLowerCase()} already uses, so your receptionist can work with them.`}
      />

      {!canManage && (
        <Alert tone="info" title="View only">
          Only the account&apos;s owners and admins can connect or disconnect integrations.
        </Alert>
      )}

      {catalog.error && (
        <Alert tone="danger" title="We couldn't load your integrations">
          {catalog.error}
        </Alert>
      )}

      {catalog.loading && !catalog.data ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((key) => (
            <Skeleton key={key} className="h-56 rounded-2xl" />
          ))}
        </div>
      ) : (
        groups.map(([category, entries]) => (
          <section key={category} aria-labelledby={`integrations-${category}`}>
            <h2 id={`integrations-${category}`} className="text-sm font-semibold text-muted">
              {CATEGORY_LABELS[category] ?? category}
            </h2>
            <div className="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {entries.map((entry) => (
                <IntegrationCard
                  key={entry.id}
                  entry={entry}
                  canManage={canManage}
                  busy={busy === entry.id}
                  tenantId={tenantId}
                  onConnect={() => void connect(entry)}
                  onVerify={() => void verify(entry)}
                  onDisconnect={() => setConfirming(entry)}
                  onChanged={() => catalog.reload()}
                />
              ))}
            </div>
          </section>
        ))
      )}

      <ConfirmDialog
        open={confirming !== null}
        title={confirming ? `Disconnect ${confirming.name}?` : "Disconnect"}
        confirmLabel="Disconnect"
        busy={confirming !== null && busy === confirming.id}
        onCancel={() => setConfirming(null)}
        onConfirm={() => confirming && void disconnect(confirming)}
      >
        Your receptionist will stop using it straight away, and we&apos;ll delete the access it
        granted. You can connect it again at any time.
      </ConfirmDialog>
    </div>
  );
}

function IntegrationCard({
  entry,
  canManage,
  busy,
  tenantId,
  onConnect,
  onVerify,
  onDisconnect,
  onChanged,
}: {
  entry: IntegrationView;
  canManage: boolean;
  busy: boolean;
  tenantId: string;
  onConnect: () => void;
  onVerify: () => void;
  onDisconnect: () => void;
  onChanged: () => void;
}) {
  const Icon = ICONS[entry.icon] ?? PlugIcon;
  const connected = entry.status === "connected";
  const broken = entry.status === "error";

  return (
    <Card className={cx("flex flex-col p-5", connected && "border-success/40")}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-surface-2 text-ink-2">
            <Icon size={20} />
          </span>
          <div className="min-w-0">
            <h3 className="truncate text-[0.9375rem] font-semibold">{entry.name}</h3>
            <p className="truncate text-xs text-muted">by {entry.vendor}</p>
          </div>
        </div>
        <StatusBadge entry={entry} />
      </div>

      <p className="mt-4 flex-1 text-sm leading-relaxed text-muted">{entry.description}</p>

      <ul aria-label="What it can do" className="mt-4 flex flex-wrap gap-1.5">
        {Array.from(new Set(entry.capabilities.map((key) => CAPABILITY_LABELS[key] ?? key))).map(
          (label) => (
            <li key={label} className="rounded-full bg-surface-2 px-2.5 py-1 text-xs font-medium text-ink-2">
              {label}
            </li>
          ),
        )}
      </ul>

      {connected && entry.account && (
        <p className="mt-4 flex items-center gap-2 text-sm text-ink-2">
          <CheckIcon size={15} className="shrink-0 text-success" />
          <span className="truncate">
            {entry.account}
            {entry.connected_at && <span className="text-muted"> · since {formatDate(entry.connected_at)}</span>}
          </span>
        </p>
      )}
      {broken && entry.last_error && (
        <p className="mt-4 text-sm text-danger">{entry.last_error}</p>
      )}
      {entry.availability === "configuration_required" && (
        <p className="mt-4 text-xs text-muted">
          This integration hasn&apos;t been set up on our side yet. It will be available once it is.
        </p>
      )}

      {connected && entry.bookable && canManage && (
        <CalendarPicker entry={entry} tenantId={tenantId} onChanged={onChanged} />
      )}

      {canManage && entry.availability === "available" && (
        <div className="mt-5 flex flex-wrap gap-2 border-t border-line pt-4">
          {connected ? (
            <>
              <Button size="sm" variant="secondary" loading={busy} onClick={onVerify}>
                Test connection
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={onDisconnect}>
                Disconnect
              </Button>
            </>
          ) : broken ? (
            <>
              <Button size="sm" loading={busy} onClick={onConnect}>
                Reconnect
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={onDisconnect}>
                Disconnect
              </Button>
            </>
          ) : (
            <Button size="sm" loading={busy} onClick={onConnect}>
              Connect
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}

function StatusBadge({ entry }: { entry: IntegrationView }) {
  if (entry.availability === "coming_soon") return <Badge tone="neutral">Coming soon</Badge>;
  if (entry.status === "connected") {
    return (
      <Badge tone="success" dot>
        Connected
      </Badge>
    );
  }
  if (entry.status === "error") return <Badge tone="danger">Needs attention</Badge>;
  if (entry.availability === "configuration_required") {
    return <Badge tone="warn">Configuration required</Badge>;
  }
  return <Badge tone="neutral">Not connected</Badge>;
}

/**
 * Which calendar in the connected account the receptionist books into.
 *
 * The list is fetched only when the owner asks for it, because reading it is a
 * live call to Google or Microsoft and most visits to this page are not about
 * changing calendars.
 */
function CalendarPicker({
  entry,
  tenantId,
  onChanged,
}: {
  entry: IntegrationView;
  tenantId: string;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [options, setOptions] = useState<CalendarOption[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function open() {
    setLoading(true);
    setProblem(null);
    try {
      const list = await listCalendars(tenantId, entry.id);
      setOptions(list.calendars);
    } catch (caught) {
      setProblem(
        caught instanceof ApiError ? caught.message : "We couldn't read your calendars.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function choose(calendarId: string) {
    setSaving(true);
    try {
      await chooseCalendar(tenantId, entry.id, calendarId);
      toast({
        tone: "success",
        title: "Calendar chosen",
        description: "Your receptionist will book appointments into it from the next call.",
      });
      setOptions(null);
      onChanged();
    } catch (caught) {
      toast({
        tone: "danger",
        title: "Couldn't use that calendar",
        description: caught instanceof ApiError ? caught.message : "Please try again.",
      });
    } finally {
      setSaving(false);
    }
  }

  if (options) {
    return (
      <div className="mt-4 border-t border-line pt-4">
        <SelectField
          label="Book appointments into"
          hint="Only calendars you can add events to can be chosen."
          defaultValue={options.find((option) => option.name === entry.calendar_name)?.id ?? ""}
          disabled={saving}
          onChange={(event) => event.target.value && void choose(event.target.value)}
          options={[
            { value: "", label: "Choose a calendar…" },
            ...options.map((option) => ({
              value: option.writable ? option.id : "",
              label: option.writable
                ? `${option.name}${option.primary ? " (default)" : ""}`
                : `${option.name} — read only`,
            })),
          ]}
        />
      </div>
    );
  }

  return (
    <div className="mt-4 border-t border-line pt-4">
      {entry.calendar_name ? (
        <p className="text-sm text-ink-2">
          Booking into <span className="font-medium">{entry.calendar_name}</span>
        </p>
      ) : (
        <p className="text-sm text-muted">
          Choose a calendar to let your receptionist book appointments.
        </p>
      )}
      {problem && <p className="mt-2 text-sm text-danger">{problem}</p>}
      <Button
        className="mt-2"
        size="sm"
        variant="secondary"
        loading={loading}
        onClick={() => void open()}
      >
        {entry.calendar_name ? "Change calendar" : "Choose calendar"}
      </Button>
    </div>
  );
}
