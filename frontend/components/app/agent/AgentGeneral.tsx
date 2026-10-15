"use client";

/**
 * Agents → General: what the agent is called, where it thinks it is, and the
 * number callers reach it on.
 *
 * Two fields that behave differently underneath, and the page says so rather
 * than pretending they are the same kind of setting:
 *
 * - **Name** is a label. It is never spoken and never reaches the prompt, so
 *   saving it does not publish a new configuration.
 * - **Timezone** is read by the agent. Opening hours are stored *in* it, so
 *   changing it republishes — and the form warns before it does.
 *
 * The number is read-only here. Buying, releasing and forwarding a number all
 * live on the Phone page already, and a second set of controls for them is a
 * second way to get it wrong.
 */

import { useMemo, useState, type ComponentType, type FormEvent } from "react";

import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { useLoad } from "@/components/app/useLoad";
import { Alert } from "@/components/ui/Alert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Feedback";
import { SelectField, TextField } from "@/components/ui/Field";
import { ClockIcon, PhoneIcon, UserIcon, type IconProps } from "@/components/ui/icons";
import { useToast } from "@/components/ui/Toast";
import { ApiError, listTimezones, updateAgentSettings } from "@/lib/api";
import { formatPhone } from "@/lib/format";
import type { AgentSettingsUpdate, GreetingStyle } from "@/lib/types";

/** A card heading with its icon, so the page matches the section nav. */
function Heading({ icon: Icon, children }: { icon: ComponentType<IconProps>; children: string }) {
  return (
    <span className="flex items-center gap-2">
      <Icon size={18} className="text-muted" />
      {children}
    </span>
  );
}

const NAME_MAX_LENGTH = 80;

/** The agent's local time, for the zone picker. Empty if the browser balks. */
function timeIn(timezone: string): string {
  try {
    // en-US rather than the browser's locale: every number this product sells
    // is North American and every zone offered is a US one, so a 24-hour clock
    // here would be the odd one out on the page.
    return new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: timezone,
    }).format(new Date());
  } catch {
    // An unknown zone in this browser's ICU data is not worth breaking the
    // picker over; the label alone still identifies it.
    return "";
  }
}

export function AgentGeneral() {
  const { tenant, phone, profile, tenantId, refresh } = useTenantData();
  const canManage = useCanManage();
  const toast = useToast();
  const zones = useLoad(() => listTimezones(tenantId), `timezones:${tenantId}`);

  const [name, setName] = useState(profile?.agent_name ?? "");
  const [timezone, setTimezone] = useState(tenant.timezone);
  const [nameError, setNameError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "warn"; text: string } | null>(null);

  const savedName = profile?.agent_name ?? "";
  const nameChanged = name.trim() !== savedName;
  const zoneChanged = timezone !== tenant.timezone;
  const dirty = nameChanged || zoneChanged;

  /**
   * The rest of the agent's settings, sent back untouched.
   *
   * The endpoint takes the whole configuration, so a partial save would blank
   * whatever it left out. When the profile has not loaded we have nothing
   * truthful to send, and saving is disabled rather than sending defaults that
   * would overwrite real answers.
   */
  const carried = useMemo(() => {
    if (!profile) return null;
    const services = profile.services.join(", ");
    const hours = profile.hours_raw ?? "";
    if (!services.trim() || !hours.trim()) return null;
    return {
      services,
      operating_hours: hours,
      greeting_style: profile.greeting_style as GreetingStyle,
      custom_greeting: profile.custom_greeting ?? "",
      escalation_rules: profile.escalation_raw ?? "",
    };
  }, [profile]);

  const options = useMemo(() => {
    const list = (zones.data ?? []).map((zone) => {
      const now = timeIn(zone.timezone);
      return { value: zone.timezone, label: now ? `${zone.label} — ${now}` : zone.label };
    });
    // Keep whatever the tenant is actually set to selectable, even if it is no
    // longer offered: the select must never silently show a different zone
    // from the one in the database.
    if (list.length && !list.some((option) => option.value === tenant.timezone)) {
      list.unshift({ value: tenant.timezone, label: `${tenant.timezone} (current)` });
    }
    return list;
  }, [zones.data, tenant.timezone]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!carried || !dirty || saving) return;

    const trimmed = name.trim();
    if (trimmed.length > NAME_MAX_LENGTH) {
      setNameError(`Keep the name under ${NAME_MAX_LENGTH} characters.`);
      return;
    }
    setNameError(null);
    setSaving(true);
    setResult(null);

    const payload: AgentSettingsUpdate = { ...carried, agent_name: trimmed };
    // Omitted rather than sent unchanged, so a no-op save cannot look like a
    // timezone edit in the audit trail.
    if (zoneChanged) payload.timezone = timezone;

    try {
      const outcome = await updateAgentSettings(tenantId, payload);
      setResult({
        tone: outcome.synced ? "success" : "warn",
        text: outcome.detail,
      });
      toast({
        tone: outcome.synced ? "success" : "info",
        title: zoneChanged ? `Version ${outcome.config_version} is live` : "Saved",
        description: outcome.detail,
      });
      await refresh();
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : "Something went wrong. Try again.";
      setResult({ tone: "warn", text: message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-6">
      <Card>
        <CardHeader
          title={<Heading icon={UserIcon}>Agent name</Heading>}
          description="For your reference only. Callers won't hear this."
        />
        <div className="p-4 pt-0">
          <TextField
            label="Name"
            value={name}
            maxLength={NAME_MAX_LENGTH}
            disabled={!canManage}
            placeholder={tenant.name}
            error={nameError ?? undefined}
            hint={`Leave this blank to use your business name, ${tenant.name}.`}
            onChange={(event) => {
              setName(event.target.value);
              setNameError(null);
            }}
          />
        </div>
      </Card>

      <Card>
        <CardHeader
          title={<Heading icon={ClockIcon}>Agent&apos;s time zone</Heading>}
          description="Used for your opening hours, so your receptionist knows when you're open."
        />
        <div className="space-y-3 p-4 pt-0">
          {zones.loading ? (
            <Skeleton className="h-10 w-full max-w-sm" />
          ) : zones.error ? (
            <Alert tone="warn">
              The time zone list didn&apos;t load, so it can&apos;t be changed right now.
            </Alert>
          ) : (
            <SelectField
              label="Time zone"
              value={timezone}
              disabled={!canManage}
              options={options}
              onChange={(event) => setTimezone(event.target.value)}
              hint="Your hours were set from your area code, which is approximate for states that span two zones."
            />
          )}
          {zoneChanged ? (
            <Alert tone="warn">
              Changing the time zone republishes your receptionist, because your opening hours are
              stored in it.
            </Alert>
          ) : null}
        </div>
      </Card>

      <Card>
        <CardHeader
          title={<Heading icon={PhoneIcon}>Agent phone number</Heading>}
          description="Forward calls to this number, or use it directly for your business."
        />
        <div className="space-y-3 p-4 pt-0">
          {phone ? (
            <p className="text-lg font-semibold text-ink">{formatPhone(phone.e164)}</p>
          ) : (
            <p className="text-sm text-muted">No number yet.</p>
          )}
          <ButtonLink href="/dashboard/settings/phone" variant="secondary">
            {phone ? "Forwarding instructions" : "Set up a number"}
          </ButtonLink>
        </div>
      </Card>

      {result ? <Alert tone={result.tone}>{result.text}</Alert> : null}

      {canManage ? (
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={!dirty || saving || !carried}>
            {saving ? "Saving…" : "Save changes"}
          </Button>
          {dirty && !saving ? (
            <button
              type="button"
              className="text-sm text-muted hover:text-ink"
              onClick={() => {
                setName(savedName);
                setTimezone(tenant.timezone);
                setNameError(null);
                setResult(null);
              }}
            >
              Discard
            </button>
          ) : null}
          {!carried ? (
            <span className="text-sm text-muted">
              Finish setting up your receptionist before changing this.
            </span>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-muted">Only an owner or admin can change these.</p>
      )}
    </form>
  );
}
