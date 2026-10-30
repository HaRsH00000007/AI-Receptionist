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
 * - **The greeting** is spoken word for word. Blank is not empty: it means
 *   "write one for me", and the line setup wrote is shown as the placeholder
 *   so the field can say which it is.
 * - **Personality** — voice, talking speed, tone and responsiveness — is how
 *   it sounds rather than what it says, and every one of them changes what a
 *   caller hears, so each publishes a new version. The speed and the
 *   responsiveness are the voice vendor's own settings, within the range it
 *   actually accepts: it refuses a speed outside 0.7 to 1.2, and it has three
 *   responsiveness settings rather than four, which is why there is no
 *   "Medium" beside "Auto" here.
 * - **The goodbye** is an instruction, not a script. A voice agent has a first
 *   message and no last one, so this is rendered into the prompt and followed
 *   rather than read out — the field says so instead of letting the two look
 *   like the same kind of promise.
 *
 * The number is read-only here. Buying, releasing and forwarding a number all
 * live on the Phone page already, and a second set of controls for them is a
 * second way to get it wrong.
 */

import { useMemo, useState, type ComponentType, type FormEvent } from "react";

import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { VoicePicker } from "@/components/app/VoicePicker";
import { useLoad } from "@/components/app/useLoad";
import { Alert } from "@/components/ui/Alert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { cx } from "@/components/ui/cx";
import { Skeleton } from "@/components/ui/Feedback";
import { SelectField, TextAreaField, TextField } from "@/components/ui/Field";
import {
  ClockIcon,
  MessageIcon,
  MicIcon,
  PhoneIcon,
  UserIcon,
  type IconProps,
} from "@/components/ui/icons";
import { useToast } from "@/components/ui/Toast";
import { ApiError, listAgentVoices, listTimezones, updateAgentSettings } from "@/lib/api";
import { formatPhone } from "@/lib/format";
import { GREETING_MAX_LENGTH } from "@/lib/greetings";
import {
  GREETING_STYLES,
  SPEAKING_SPEEDS,
  type AgentSettingsUpdate,
  type GreetingStyle,
  type Responsiveness,
} from "@/lib/types";

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
  const catalogue = useLoad(() => listAgentVoices(tenantId), `voices:${tenantId}`);

  const [name, setName] = useState(profile?.agent_name ?? "");
  const [timezone, setTimezone] = useState(tenant.timezone);
  const [greeting, setGreeting] = useState(profile?.custom_greeting ?? "");
  const [goodbye, setGoodbye] = useState(profile?.custom_goodbye ?? "");
  // Null until somebody picks one, so the selection can fall back to the
  // voice actually in force once the catalogue says which that is.
  const [picked, setPicked] = useState<string | null>(profile?.voice_id ?? null);
  const [speed, setSpeed] = useState<number>(profile?.speaking_speed ?? 1);
  const [tone, setTone] = useState<GreetingStyle>(
    (profile?.greeting_style ?? "professional") as GreetingStyle,
  );
  const [answers, setAnswers] = useState<Responsiveness>(profile?.responsiveness ?? "auto");
  const [nameError, setNameError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "warn"; text: string } | null>(null);

  const savedName = profile?.agent_name ?? "";
  const savedGreeting = profile?.custom_greeting ?? "";
  const savedGoodbye = profile?.custom_goodbye ?? "";
  /**
   * The voice in force, which is not always the voice on the profile.
   *
   * A business that never picked one is published with its greeting style's
   * default, so showing nothing selected would be showing a receptionist with
   * no voice. The style's voice is marked instead, and because it is also
   * treated as "saved", simply opening the page does not look like an edit.
   */
  const styleDefault = useMemo(
    () => (catalogue.data ?? []).find((option) => option.style === profile?.greeting_style),
    [catalogue.data, profile?.greeting_style],
  );
  const savedVoice = profile?.voice_id ?? styleDefault?.voice_id ?? "";
  // Derived rather than synchronised in an effect: the catalogue arrives after
  // the first render, and an effect that copied it into state would be a
  // second render saying the same thing.
  const voiceId = picked ?? savedVoice;
  // Null means "the vendor's default", which is 1 and "auto" — so an untouched
  // form is not an edit even though it shows a value.
  const savedSpeed = profile?.speaking_speed ?? 1;
  const savedTone = (profile?.greeting_style ?? "professional") as GreetingStyle;
  const savedAnswers = profile?.responsiveness ?? "auto";
  const nameChanged = name.trim() !== savedName;
  const zoneChanged = timezone !== tenant.timezone;
  const greetingChanged = greeting.trim() !== savedGreeting;
  const goodbyeChanged = goodbye.trim() !== savedGoodbye;
  const voiceChanged = voiceId !== savedVoice;
  const speedChanged = speed !== savedSpeed;
  const toneChanged = tone !== savedTone;
  const answersChanged = answers !== savedAnswers;
  const soundsDifferent = voiceChanged || speedChanged || toneChanged || answersChanged;
  const dirty = nameChanged || zoneChanged || greetingChanged || goodbyeChanged || soundsDifferent;

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

    const payload: AgentSettingsUpdate = {
      ...carried,
      agent_name: trimmed,
      greeting_style: tone,
      custom_greeting: greeting.trim(),
      custom_goodbye: goodbye.trim(),
    };
    // Each omitted unless it moved, so an untouched setting cannot look like an
    // edit in the audit trail — the same rule the timezone follows.
    if (speedChanged) payload.speaking_speed = speed;
    if (answersChanged) payload.responsiveness = answers;
    // Omitted unless it moved, so an unchanged voice cannot look like an edit
    // in the audit trail — the same rule the timezone follows below.
    if (voiceChanged) payload.voice_id = voiceId;
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
        <div className="space-y-4 p-4 pt-0">
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
          title={<Heading icon={MicIcon}>Personality</Heading>}
          description="Your receptionist's conversation style, voice and tone."
        />
        <div className="space-y-5 p-4 pt-0">
          <p className="field-label">Voice</p>
          {catalogue.loading && !catalogue.data ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Skeleton className="h-40 rounded-xl" />
              <Skeleton className="h-40 rounded-xl" />
              <Skeleton className="h-40 rounded-xl" />
            </div>
          ) : catalogue.error ? (
            <Alert tone="warn">
              The voice list didn&apos;t load, so it can&apos;t be changed right now.
            </Alert>
          ) : (
            <VoicePicker
              voices={catalogue.data ?? []}
              voiceId={voiceId}
              // So "Hear it" demonstrates the speed being chosen below rather
              // than a speed nobody selected.
              speed={speed}
              disabled={!canManage}
              onPick={setPicked}
            />
          )}

          <Segmented
            legend="Talking speed"
            value={String(speed)}
            disabled={!canManage}
            options={SPEAKING_SPEEDS.map((option) => ({
              value: String(option),
              label: String(option),
              /* 1 is the recording as the vendor made it; every other value is
                 the same recording stretched or squeezed. */
              note: option === 1 ? "Most lifelike" : undefined,
            }))}
            onChange={(next) => setSpeed(Number(next))}
            hint="The samples above play at this speed, so you can hear it before you save."
          />

          <SelectField
            label="Tone"
            value={tone}
            disabled={!canManage}
            options={GREETING_STYLES.map((option) => ({
              value: option.value,
              label: option.label,
            }))}
            onChange={(event) => setTone(event.target.value as GreetingStyle)}
            hint="How it speaks to callers. This also picks the voice when you haven't chosen one."
            className="max-w-sm"
          />

          <Segmented
            legend="Responsiveness"
            value={answers}
            disabled={!canManage}
            options={[
              { value: "auto", label: "Auto" },
              { value: "low", label: "Low" },
              { value: "high", label: "High" },
            ]}
            onChange={(next) => setAnswers(next as Responsiveness)}
            hint="How quickly your agent decides the caller has finished speaking and replies. Auto uses the recommended setting; Low waits longer for callers who pause."
          />

          {soundsDifferent ? (
            <Alert tone="warn">Saving this publishes a new version of your receptionist.</Alert>
          ) : null}
        </div>
      </Card>

      <Card>
        <CardHeader
          title={<Heading icon={MessageIcon}>Greeting and goodbye</Heading>}
          description="The first thing a caller hears, and how the call ends."
        />
        <div className="space-y-3 p-4 pt-0">
          <TextAreaField
            label="Default greeting message"
            rows={3}
            maxLength={GREETING_MAX_LENGTH}
            value={greeting}
            disabled={!canManage}
            placeholder={profile?.greeting ?? ""}
            onChange={(event) => setGreeting(event.target.value)}
            hint="Leave it blank and we'll keep the one written from your business details, shown above in grey."
          />
          <TextAreaField
            label="Goodbye message"
            optional
            rows={2}
            maxLength={GREETING_MAX_LENGTH}
            value={goodbye}
            disabled={!canManage}
            placeholder="Thanks for calling. Have a great day."
            onChange={(event) => setGoodbye(event.target.value)}
            hint="Your receptionist is told to sign off with this. Unlike the greeting above, it's an instruction rather than a script, so the wording can vary slightly."
          />
          {greetingChanged || goodbyeChanged ? (
            <Alert tone="warn">Saving this publishes a new version of your receptionist.</Alert>
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
                setGreeting(savedGreeting);
                setGoodbye(savedGoodbye);
                setPicked(profile?.voice_id ?? null);
                setSpeed(savedSpeed);
                setTone(savedTone);
                setAnswers(savedAnswers);
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

/**
 * A row of mutually exclusive choices, as in the reference design.
 *
 * Real radio inputs underneath rather than buttons: a group of buttons is
 * announced as a group of buttons, and this is one question with one answer.
 * `note` marks an option with a word of its own — "Most lifelike" under the
 * unaltered speed — which is a property of that option, not of the selection,
 * so it is shown whether or not it is the one chosen.
 */
function Segmented({
  legend,
  value,
  options,
  disabled,
  hint,
  onChange,
}: {
  legend: string;
  value: string;
  options: readonly { value: string; label: string; note?: string }[];
  disabled?: boolean;
  hint?: string;
  onChange: (value: string) => void;
}) {
  return (
    <fieldset>
      <legend className="field-label">{legend}</legend>
      <div className="mt-1.5 inline-flex flex-wrap gap-1 rounded-xl bg-surface-2 p-1">
        {options.map((option) => {
          const chosen = option.value === value;
          return (
            <label
              key={option.value}
              className={cx(
                "flex cursor-pointer flex-col items-center rounded-lg px-3.5 py-1.5 text-sm transition-colors",
                chosen
                  ? "bg-surface font-semibold text-ink shadow-xs"
                  : "text-muted hover:text-ink",
                disabled && "cursor-not-allowed opacity-60",
              )}
            >
              <input
                type="radio"
                name={legend}
                className="sr-only"
                value={option.value}
                checked={chosen}
                disabled={disabled}
                onChange={() => onChange(option.value)}
              />
              <span>{option.label}</span>
              {option.note ? (
                <span className="text-[0.625rem] font-medium uppercase tracking-wide text-success">
                  {option.note}
                </span>
              ) : null}
            </label>
          );
        })}
      </div>
      {hint ? <p className="field-hint">{hint}</p> : null}
    </fieldset>
  );
}
