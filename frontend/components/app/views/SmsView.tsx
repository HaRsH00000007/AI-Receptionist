"use client";

/**
 * SMS Campaigns — gated on carrier registration.
 *
 * US carriers block application-to-person texts from a 10-digit number that
 * isn't registered to a vetted brand and campaign (A2P 10DLC). So this page is,
 * first, the path through that registration:
 *
 *     number live -> registration -> submitted -> under review
 *         -> approved | rejected -> enabled -> campaigns
 *
 * The customer fills in and submits the registration. Everything after that is
 * decided by review, and the page only ever reports it. Campaign controls obey
 * one flag from the API, `can_send`, which is true only once SMS is enabled;
 * nothing here can switch it on.
 */

import { useState, type FormEvent, type ReactNode } from "react";

import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { useLoad } from "@/components/app/useLoad";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { cx } from "@/components/ui/cx";
import { EmptyState, Skeleton } from "@/components/ui/Feedback";
import { ChoiceGroup, SelectField, TextAreaField, TextField } from "@/components/ui/Field";
import { CheckIcon, LockIcon, SendIcon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, getSmsState, saveSmsRegistration, submitSmsRegistration } from "@/lib/api";
import { formatDate, formatPhone } from "@/lib/format";
import type { Tone } from "@/lib/tone";
import {
  SMS_USE_CASES,
  type SmsBrandType,
  type SmsRegistrationInput,
  type SmsRegistrationView,
  type SmsState,
  type SmsStateView,
  type SmsUseCase,
} from "@/lib/types";

const STATE_COPY: Record<SmsState, { label: string; tone: Tone; description: string }> = {
  not_configured: {
    label: "Not configured",
    tone: "neutral",
    description: "Texting becomes available once your receptionist's phone number is live.",
  },
  compliance_required: {
    label: "Registration required",
    tone: "warn",
    description:
      "US carriers require every business to register before sending texts from a local number. It takes a few minutes to fill in.",
  },
  draft: {
    label: "Draft",
    tone: "neutral",
    description: "Your registration is saved. Finish it and submit it for review.",
  },
  submitted: {
    label: "Submitted",
    tone: "accent",
    description: "We've received your registration and are preparing it for carrier review.",
  },
  under_review: {
    label: "Under review",
    tone: "accent",
    description:
      "The carriers are reviewing your registration. This can take from a few days to a few weeks, and we'll update this page as soon as it changes.",
  },
  approved: {
    label: "Approved",
    tone: "success",
    description: "Your registration was approved. We're connecting your number to it now.",
  },
  rejected: {
    label: "Changes needed",
    tone: "danger",
    description: "Your registration wasn't approved. Update the details below and submit it again.",
  },
  enabled: {
    label: "SMS enabled",
    tone: "success",
    description: "Your number is registered and can send texts.",
  },
};

/** The path, drawn as steps. Rejection is shown on the review step. */
const STEPS: { key: string; label: string; reached: readonly SmsState[] }[] = [
  {
    key: "number",
    label: "Number live",
    reached: ["compliance_required", "draft", "submitted", "under_review", "approved", "rejected", "enabled"],
  },
  { key: "registration", label: "Registration", reached: ["submitted", "under_review", "approved", "rejected", "enabled"] },
  { key: "review", label: "Carrier review", reached: ["approved", "enabled"] },
  { key: "approved", label: "Approved", reached: ["approved", "enabled"] },
  { key: "enabled", label: "SMS enabled", reached: ["enabled"] },
];

const FIELD_LABELS: Record<string, string> = {
  legal_business_name: "Legal business name",
  tax_id: "EIN",
  website: "Website",
  address_line1: "Street address",
  city: "City",
  region: "State",
  postal_code: "ZIP code",
  contact_name: "Contact name",
  contact_email: "Contact email",
  contact_phone: "Contact phone",
  use_case: "What you'll text about",
  campaign_description: "Campaign description (at least 40 characters)",
  opt_in_description: "How people agree to texts (at least 40 characters)",
  sample_messages: "Two sample messages (at least 20 characters each)",
  sample_messages_opt_out: "A sample message with opt-out wording, like “Reply STOP to opt out”",
};

type Form = SmsRegistrationInput;
type Errors = Partial<Record<keyof Form | "sample_messages_opt_out", string>>;

function formFrom(registration: SmsRegistrationView | null): Form {
  const samples = [...(registration?.sample_messages ?? [])];
  while (samples.length < 2) samples.push("");
  return {
    brand_type: registration?.brand_type ?? "standard",
    legal_business_name: registration?.legal_business_name ?? "",
    tax_id: registration?.tax_id ?? "",
    website: registration?.website ?? "",
    address_line1: registration?.address_line1 ?? "",
    address_line2: registration?.address_line2 ?? "",
    city: registration?.city ?? "",
    region: registration?.region ?? "",
    postal_code: registration?.postal_code ?? "",
    contact_name: registration?.contact_name ?? "",
    contact_email: registration?.contact_email ?? "",
    contact_phone: registration?.contact_phone ?? "",
    use_case: registration?.use_case ?? null,
    campaign_description: registration?.campaign_description ?? "",
    sample_messages: samples,
    opt_in_description: registration?.opt_in_description ?? "",
  };
}

/** Blank strings go to the API as null, so a draft never stores empty text. */
function payload(form: Form): SmsRegistrationInput {
  const blank = (value: string | null) => (value && value.trim() ? value.trim() : null);
  return {
    ...form,
    legal_business_name: blank(form.legal_business_name),
    tax_id: form.brand_type === "standard" ? blank(form.tax_id) : null,
    website: blank(form.website),
    address_line1: blank(form.address_line1),
    address_line2: blank(form.address_line2),
    city: blank(form.city),
    region: blank(form.region),
    postal_code: blank(form.postal_code),
    contact_name: blank(form.contact_name),
    contact_email: blank(form.contact_email),
    contact_phone: blank(form.contact_phone),
    campaign_description: blank(form.campaign_description),
    opt_in_description: blank(form.opt_in_description),
    sample_messages: form.sample_messages.map((message) => message.trim()).filter(Boolean),
  };
}

export function SmsView() {
  const { tenantId } = useTenantData();
  const canManage = useCanManage();
  const sms = useLoad(() => getSmsState(tenantId), `sms:${tenantId}`);
  const [editing, setEditing] = useState(false);

  const state = sms.data;

  return (
    <div className="space-y-6 lg:space-y-8">
      <PageHeader
        title="SMS Campaigns"
        description="Text your callers from your receptionist's number, once your business is registered with the carriers."
      />

      {sms.error && (
        <Alert tone="danger" title="We couldn't load SMS">
          {sms.error}
        </Alert>
      )}

      {!state ? (
        sms.loading && <Skeleton className="h-48 rounded-2xl" />
      ) : (
        <>
          <StatusCard
            state={state}
            canManage={canManage}
            editing={editing}
            onStart={() => setEditing(true)}
          />

          {state.editable && canManage && (editing || state.registration) ? (
            <RegistrationForm
              key={state.registration?.status ?? "new"}
              tenantId={tenantId}
              state={state}
              onSaved={(next) => {
                sms.set(next);
                if (next.state === "submitted") setEditing(false);
              }}
            />
          ) : state.registration ? (
            <RegistrationSummary registration={state.registration} />
          ) : null}

          <CampaignsCard state={state} />
        </>
      )}
    </div>
  );
}

function StatusCard({
  state,
  canManage,
  editing,
  onStart,
}: {
  state: SmsStateView;
  canManage: boolean;
  editing: boolean;
  onStart: () => void;
}) {
  const copy = STATE_COPY[state.state];
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-start sm:justify-between sm:p-6">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h2 className="text-[0.9375rem] font-semibold">Texting status</h2>
            <Badge tone={copy.tone} dot>
              {copy.label}
            </Badge>
          </div>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">{copy.description}</p>
          {state.phone_number && (
            <p className="mt-2 text-sm text-ink-2">
              Sending number: <span className="font-semibold tabular-nums">{formatPhone(state.phone_number)}</span>
            </p>
          )}
          {state.state === "rejected" && state.registration?.rejection_reason && (
            <Alert tone="danger" title="Why it wasn't approved" className="mt-4">
              {state.registration.rejection_reason}
            </Alert>
          )}
        </div>
        {state.state === "compliance_required" && canManage && !editing && (
          <Button onClick={onStart} className="shrink-0">
            Start registration
          </Button>
        )}
      </div>
      <ol className="grid grid-cols-5 border-t border-line bg-surface-2 text-center">
        {STEPS.map((step) => {
          const reached = step.reached.includes(state.state);
          const rejectedHere = step.key === "review" && state.state === "rejected";
          return (
            <li key={step.key} className="px-1 py-3">
              <span
                aria-hidden
                className={cx(
                  "mx-auto flex size-6 items-center justify-center rounded-full text-xs font-bold",
                  rejectedHere
                    ? "bg-danger text-white"
                    : reached
                      ? "bg-success text-white"
                      : "border border-line-strong bg-surface text-muted",
                )}
              >
                {reached ? <CheckIcon size={13} /> : rejectedHere ? "!" : ""}
              </span>
              <span className="mt-1.5 block text-[0.6875rem] font-semibold text-ink-2 sm:text-xs">
                {step.label}
                <span className="sr-only">{reached ? " (done)" : rejectedHere ? " (changes needed)" : ""}</span>
              </span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

function RegistrationForm({
  tenantId,
  state,
  onSaved,
}: {
  tenantId: string;
  state: SmsStateView;
  onSaved: (next: SmsStateView) => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<Form>(() => formFrom(state.registration));
  const [errors, setErrors] = useState<Errors>({});
  const [working, setWorking] = useState<"save" | "submit" | null>(null);

  function update<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
    setErrors((previous) => ({ ...previous, [key]: undefined }));
  }

  async function save(submit: boolean) {
    setWorking(submit ? "submit" : "save");
    setErrors({});
    try {
      let next = await saveSmsRegistration(tenantId, payload(form));
      if (submit) next = await submitSmsRegistration(tenantId);
      onSaved(next);
      toast({
        tone: "success",
        title: submit ? "Registration submitted" : "Draft saved",
        description: submit
          ? "We'll prepare it for carrier review and update this page as it progresses."
          : "Come back any time to finish it.",
      });
    } catch (caught) {
      const failure = caught instanceof ApiError ? caught : null;
      const missing = failure?.code === "sms_registration_incomplete" ? missingFrom(failure) : [];
      if (missing.length) {
        setErrors(
          Object.fromEntries(missing.map((field) => [field, "Required to submit."])) as Errors,
        );
      } else if (failure && Object.keys(failure.fieldErrors).length) {
        setErrors(failure.fieldErrors as Errors);
      }
      toast({
        tone: "danger",
        title: submit ? "Not submitted yet" : "Couldn't save",
        description: missing.length
          ? `Still needed: ${missing.map((field) => FIELD_LABELS[field] ?? field).join("; ")}.`
          : (failure?.message ?? "Please try again."),
      });
    } finally {
      setWorking(null);
    }
  }

  const standard = form.brand_type === "standard";

  return (
    <Card>
      <CardHeader
        title="Business registration"
        description="What carriers need to approve your texts. Save a draft any time; submit when it's complete."
      />
      <form
        className="space-y-8 p-5 sm:p-6"
        noValidate
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          void save(true);
        }}
      >
        <Group title="Your business">
          <ChoiceGroup<SmsBrandType>
            legend="Business type"
            name="brand_type"
            value={form.brand_type}
            onChange={(value) => update("brand_type", value)}
            options={[
              {
                value: "standard",
                label: "Registered business",
                description: "Has an EIN. Higher sending limits.",
              },
              {
                value: "sole_proprietor",
                label: "Sole proprietor",
                description: "No EIN. Lower limits, one number.",
              },
            ]}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Legal business name"
              value={form.legal_business_name ?? ""}
              onChange={(event) => update("legal_business_name", event.target.value)}
              error={errors.legal_business_name}
            />
            {standard && (
              <TextField
                label="EIN"
                value={form.tax_id ?? ""}
                onChange={(event) => update("tax_id", event.target.value)}
                placeholder="12-3456789"
                error={errors.tax_id}
              />
            )}
            <TextField
              label="Website"
              optional={!standard}
              value={form.website ?? ""}
              onChange={(event) => update("website", event.target.value)}
              placeholder="https://yourbusiness.com"
              error={errors.website}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Street address"
              value={form.address_line1 ?? ""}
              onChange={(event) => update("address_line1", event.target.value)}
              error={errors.address_line1}
              className="sm:col-span-2"
            />
            <TextField
              label="Suite or unit"
              optional
              value={form.address_line2 ?? ""}
              onChange={(event) => update("address_line2", event.target.value)}
              className="sm:col-span-2"
            />
            <TextField
              label="City"
              value={form.city ?? ""}
              onChange={(event) => update("city", event.target.value)}
              error={errors.city}
            />
            <div className="grid grid-cols-2 gap-4">
              <TextField
                label="State"
                value={form.region ?? ""}
                onChange={(event) => update("region", event.target.value)}
                placeholder="CA"
                error={errors.region}
              />
              <TextField
                label="ZIP code"
                value={form.postal_code ?? ""}
                onChange={(event) => update("postal_code", event.target.value)}
                error={errors.postal_code}
              />
            </div>
          </div>
        </Group>

        <Group title="Who carriers can contact">
          <div className="grid gap-4 sm:grid-cols-3">
            <TextField
              label="Name"
              value={form.contact_name ?? ""}
              onChange={(event) => update("contact_name", event.target.value)}
              error={errors.contact_name}
            />
            <TextField
              label="Email"
              type="email"
              value={form.contact_email ?? ""}
              onChange={(event) => update("contact_email", event.target.value)}
              error={errors.contact_email}
            />
            <TextField
              label="Phone"
              type="tel"
              value={form.contact_phone ?? ""}
              onChange={(event) => update("contact_phone", event.target.value)}
              error={errors.contact_phone}
            />
          </div>
        </Group>

        <Group title="What you'll text about">
          <SelectField
            label="Use case"
            value={form.use_case ?? ""}
            options={[{ value: "", label: "Choose one" }, ...SMS_USE_CASES]}
            onChange={(event) => update("use_case", (event.target.value || null) as SmsUseCase | null)}
            error={errors.use_case}
          />
          <TextAreaField
            label="Campaign description"
            rows={3}
            value={form.campaign_description ?? ""}
            onChange={(event) => update("campaign_description", event.target.value)}
            hint="Who receives your texts and why — for example, appointment confirmations for clients who booked by phone."
            error={errors.campaign_description}
          />
          <TextAreaField
            label="How people agree to receive texts"
            rows={3}
            value={form.opt_in_description ?? ""}
            onChange={(event) => update("opt_in_description", event.target.value)}
            hint="Carriers check this closely. Describe exactly when and how someone says yes."
            error={errors.opt_in_description}
          />
          <div className="space-y-4">
            {form.sample_messages.map((message, index) => (
              <TextAreaField
                key={index}
                label={`Sample message ${index + 1}`}
                rows={2}
                maxLength={320}
                value={message}
                onChange={(event) => {
                  const next = [...form.sample_messages];
                  next[index] = event.target.value;
                  update("sample_messages", next);
                }}
                hint={index === 0 ? "Include your business name, and “Reply STOP to opt out” in at least one." : undefined}
                error={index === 0 ? (errors.sample_messages ?? errors.sample_messages_opt_out) : undefined}
              />
            ))}
            {form.sample_messages.length < 5 && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => update("sample_messages", [...form.sample_messages, ""])}
              >
                Add another sample
              </Button>
            )}
          </div>
        </Group>

        <div className="flex flex-wrap justify-end gap-2.5 border-t border-line pt-5">
          <Button variant="secondary" loading={working === "save"} disabled={working !== null} onClick={() => void save(false)}>
            Save draft
          </Button>
          <Button type="submit" loading={working === "submit"} disabled={working !== null}>
            Submit for review
          </Button>
        </div>
      </form>
    </Card>
  );
}

function missingFrom(error: ApiError): string[] {
  // The API names each field it still needs; see `missing_for_submission`.
  const missing = error.details["missing"];
  return Array.isArray(missing) ? missing.filter((field): field is string => typeof field === "string") : [];
}

function RegistrationSummary({ registration }: { registration: SmsRegistrationView }) {
  const useCase = SMS_USE_CASES.find((entry) => entry.value === registration.use_case)?.label;
  return (
    <Card>
      <CardHeader
        title="Business registration"
        description={
          registration.submitted_at ? `Submitted ${formatDate(registration.submitted_at)}` : undefined
        }
      />
      <dl className="grid gap-x-6 gap-y-5 p-5 text-sm sm:grid-cols-2">
        <Detail label="Legal business name">{registration.legal_business_name ?? "—"}</Detail>
        <Detail label="Business type">
          {registration.brand_type === "standard" ? "Registered business" : "Sole proprietor"}
        </Detail>
        <Detail label="Website">{registration.website ?? "—"}</Detail>
        <Detail label="Use case">{useCase ?? "—"}</Detail>
        <Detail label="Contact">
          {[registration.contact_name, registration.contact_email].filter(Boolean).join(" · ") || "—"}
        </Detail>
        <Detail label="Sample messages">{registration.sample_messages.length}</Detail>
      </dl>
    </Card>
  );
}

function CampaignsCard({ state }: { state: SmsStateView }) {
  return (
    <Card>
      <CardHeader
        title="Campaigns"
        action={
          <Button size="sm" disabled leadingIcon={state.can_send ? <SendIcon size={15} /> : <LockIcon size={15} />}>
            New campaign
          </Button>
        }
      />
      <div className="p-5">
        <EmptyState
          icon={state.can_send ? <SendIcon /> : <LockIcon />}
          title={state.can_send ? "Campaigns are coming soon" : "Campaigns unlock once SMS is enabled"}
          description={
            state.can_send
              ? "Your number is registered. Creating and sending campaigns is the next thing we're building."
              : "Texts can only be sent after your registration is approved and your number is enabled. That protects your number from being blocked by carriers."
          }
          action={
            !state.can_send && state.state === "not_configured" ? (
              <ButtonLink href="/dashboard/agent" variant="secondary">
                View your receptionist
              </ButtonLink>
            ) : undefined
          }
        />
      </div>
    </Card>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="space-y-4">
      <legend className="text-sm font-semibold text-ink">{title}</legend>
      {children}
    </fieldset>
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
