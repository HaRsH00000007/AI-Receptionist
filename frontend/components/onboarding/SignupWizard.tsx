"use client";

/**
 * Onboarding: four steps of questions, then activation.
 *
 * It submits the same single `POST /signups` the backend has always accepted —
 * the steps are a presentation of one form, not a multi-request protocol, so
 * nothing is half-created if someone abandons it at step three.
 *
 * Validation here is deliberately thin. The backend is the authority on what a
 * valid phone number or area code is; the wizard only checks enough to stop an
 * obviously incomplete step, and renders the API's own per-field errors next
 * to the fields they name, jumping back to the step that holds them.
 */

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { GREETING_STYLE_COPY } from "@/components/app/BusinessDetails";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { cx } from "@/components/ui/cx";
import { ChoiceGroup, TextAreaField, TextField, type ChoiceOption } from "@/components/ui/Field";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  BuildingIcon,
  CheckIcon,
  HashIcon,
  HeadsetIcon,
  HomeIcon,
  MessageIcon,
  MicIcon,
  PhoneForwardIcon,
  ScaleIcon,
  ScissorsIcon,
  StethoscopeIcon,
} from "@/components/ui/icons";
import { ApiError, submitSignup } from "@/lib/api";
import { formatList, formatNumber } from "@/lib/format";
import { GREETING_MAX_LENGTH, greetingPreset, type GreetingChoice } from "@/lib/greetings";
import { PLAN_CATALOG } from "@/lib/plans";
import {
  GREETING_STYLES,
  businessTypeLabel,
  type BusinessType,
  type GreetingStyle,
  type Plan,
  type SignupRequest,
} from "@/lib/types";

import { OnboardingStepper } from "./OnboardingStepper";

type Field = keyof SignupRequest;
type Step = 0 | 1 | 2 | 3;
type PhoneMode = "new" | "forward";
type Errors = Partial<Record<Field, string>>;

const LAST_STEP: Step = 3;

const EMPTY: SignupRequest = {
  business_name: "",
  business_type: "other",
  services: "",
  operating_hours: "",
  greeting_style: "professional",
  custom_greeting: "",
  escalation_rules: "",
  notification_email: "",
  area_code: "",
  plan: "starter",
  contact_phone: "",
};

const STEP_FIELDS: Record<Step, readonly Field[]> = {
  0: ["business_name", "business_type", "services", "contact_phone"],
  1: ["greeting_style", "custom_greeting", "operating_hours", "escalation_rules", "notification_email"],
  2: ["area_code"],
  3: ["plan"],
};

const STEP_HEADINGS: Record<Step, { title: string; description: string }> = {
  0: {
    title: "Tell us about your business",
    description: "Your receptionist uses this to answer callers accurately.",
  },
  1: {
    title: "How should your receptionist answer?",
    description: "Set the tone, the opening line, your hours, and when to get you involved.",
  },
  2: {
    title: "Choose your phone number",
    description: "Get a new local number, or keep the one your customers already know.",
  },
  3: {
    title: "Review and activate",
    description: "Check the details, choose a plan, and go live.",
  },
};

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const HOURS_PRESETS = ["Mon-Fri 9-5", "Mon-Fri 8-6, Sat 9-1", "Mon-Sat 9-7"];

const ESCALATION_SUGGESTIONS = [
  "Notify me right away if a caller has an emergency.",
  "Take a message for billing questions.",
];

const BUSINESS_TYPE_OPTIONS: ReadonlyArray<ChoiceOption<BusinessType>> = [
  { value: "salon", label: businessTypeLabel("salon"), description: "Salons, spas and beauty studios", icon: <ScissorsIcon size={18} /> },
  { value: "legal", label: businessTypeLabel("legal"), description: "Law firms and legal practices", icon: <ScaleIcon size={18} /> },
  { value: "medical", label: businessTypeLabel("medical"), description: "Clinics, dental and medical offices", icon: <StethoscopeIcon size={18} /> },
  { value: "real_estate", label: businessTypeLabel("real_estate"), description: "Agents, brokerages and rentals", icon: <HomeIcon size={18} /> },
  { value: "other", label: businessTypeLabel("other"), description: "Any other business, set up from your details", icon: <BuildingIcon size={18} /> },
];

export function splitServices(value: string): string[] {
  return value
    .split(/[,;\n]/)
    .map((service) => service.trim())
    .filter(Boolean);
}

function validateField(field: Field, values: SignupRequest): string | undefined {
  switch (field) {
    case "business_name":
      return values.business_name.trim() ? undefined : "Enter your business name.";
    case "services":
      return splitServices(values.services).length ? undefined : "Add at least one service.";
    case "contact_phone":
      return values.contact_phone.replace(/\D/g, "").length >= 7
        ? undefined
        : "Enter a phone number with at least 7 digits.";
    case "operating_hours":
      return values.operating_hours.trim() ? undefined : "Tell us when you're open.";
    case "notification_email":
      return EMAIL.test(values.notification_email.trim()) ? undefined : "Enter a valid email address.";
    case "area_code":
      return /^\d{3}$/.test(values.area_code.trim()) ? undefined : "Enter a 3-digit area code.";
    default:
      return undefined;
  }
}

function stepErrors(step: Step, values: SignupRequest): Errors {
  const found: Errors = {};
  for (const field of STEP_FIELDS[step]) {
    const message = validateField(field, values);
    if (message) found[field] = message;
  }
  return found;
}

interface StepProps {
  values: SignupRequest;
  error: (field: Field) => string | undefined;
  update: <K extends Field>(field: K, value: SignupRequest[K]) => void;
}

export function SignupWizard() {
  const router = useRouter();
  const [step, setStep] = useState<Step>(0);
  const [values, setValues] = useState<SignupRequest>(EMPTY);
  const [phoneMode, setPhoneMode] = useState<PhoneMode>("new");
  // The opening line is three decisions in one: let setup write it, take the
  // ready-made line for the chosen style, or dictate it word for word. Only the
  // last two send text, and what is shown is what callers hear.
  const [greetingChoice, setGreetingChoice] = useState<GreetingChoice>("auto");
  const [customGreeting, setCustomGreeting] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [apiError, setApiError] = useState<ApiError | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(false);

  // Moving between steps replaces the whole form, so focus is moved to the new
  // step's heading — otherwise it is left on a button that no longer exists.
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    headingRef.current?.focus();
  }, [step]);

  function chosenGreeting(): string {
    if (greetingChoice === "auto") return "";
    if (greetingChoice === "preset") {
      return greetingPreset(values.greeting_style, values.business_name);
    }
    return customGreeting.trim();
  }

  function update<K extends Field>(field: K, value: SignupRequest[K]) {
    setValues((previous) => ({ ...previous, [field]: value }));
    setErrors((previous) => (previous[field] ? { ...previous, [field]: undefined } : previous));
  }

  function error(field: Field): string | undefined {
    return errors[field];
  }

  /** Check a step, replacing that step's errors. True when it is clean. */
  function checkStep(index: Step): boolean {
    const found = stepErrors(index, values);
    if (index === 1 && greetingChoice === "custom") {
      const line = customGreeting.trim();
      if (!line) {
        found.custom_greeting = "Write the line your receptionist should say, or choose another option.";
      } else if (line.length > GREETING_MAX_LENGTH) {
        found.custom_greeting = `Keep it under ${GREETING_MAX_LENGTH} characters.`;
      }
    }
    setErrors((previous) => {
      const next = { ...previous };
      for (const field of STEP_FIELDS[index]) next[field] = found[field];
      return next;
    });
    return Object.keys(found).length === 0;
  }

  async function activate() {
    // Every step again: a field can be invalid on a step the user jumped back past.
    for (const index of [0, 1, 2] as const) {
      if (!checkStep(index)) {
        setStep(index);
        return;
      }
    }

    setSubmitting(true);
    setApiError(null);
    try {
      const result = await submitSignup({
        ...values,
        business_name: values.business_name.trim(),
        notification_email: values.notification_email.trim(),
        area_code: values.area_code.trim(),
        custom_greeting: chosenGreeting(),
      });
      // The grant travels with the redirect; without it the status page
      // cannot read anything.
      const forwarding = phoneMode === "forward" ? "&setup=forward" : "";
      router.push(`/status/${result.tenant_id}?t=${encodeURIComponent(result.status_token)}${forwarding}`);
    } catch (caught) {
      const failure =
        caught instanceof ApiError ? caught : new ApiError("Something went wrong. Please try again.", { status: 0 });
      setApiError(failure);
      const fieldErrors = failure.fieldErrors as Errors;
      if (Object.keys(fieldErrors).length) {
        setErrors((previous) => ({ ...previous, ...fieldErrors }));
        const target = ([0, 1, 2, 3] as const).find((index) =>
          STEP_FIELDS[index].some((field) => fieldErrors[field]),
        );
        if (target !== undefined) setStep(target);
      }
      setSubmitting(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (step < LAST_STEP) {
      if (checkStep(step)) setStep((step + 1) as Step);
      return;
    }
    void activate();
  }

  const heading = STEP_HEADINGS[step];
  const stepProps: StepProps = { values, error, update };
  const greeting = chosenGreeting();

  return (
    <div className="mx-auto max-w-6xl">
      <div className="max-w-2xl">
        <p className="eyebrow">Get started</p>
        <h1 className="heading-xl mt-3">Set up your AI receptionist</h1>
        <p className="lead mt-3">
          A few details about your business, and your receptionist will be answering calls in minutes.
        </p>
      </div>

      <div className="mt-8">
        <OnboardingStepper current={step} onStepClick={(index) => setStep(index as Step)} />
      </div>

      <div className="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1fr)_21rem] xl:grid-cols-[minmax(0,1fr)_23rem]">
        <form noValidate onSubmit={onSubmit} className="card min-w-0 overflow-hidden shadow-sm">
          <div className="border-b border-line px-5 py-5 sm:px-8 sm:py-6">
            <p className="text-xs font-semibold text-muted">Step {step + 1} of 5</p>
            <h2 ref={headingRef} tabIndex={-1} className="mt-1 text-xl font-bold tracking-tight outline-none">
              {heading.title}
            </h2>
            <p className="mt-1 text-sm text-muted">{heading.description}</p>
          </div>

          <div key={step} className="animate-fade-in space-y-7 px-5 py-6 sm:px-8 sm:py-8">
            {apiError && (
              <Alert tone="danger" title={apiError.message}>
                {apiError.correlationId ? (
                  <>
                    Nothing was set up. Reference{" "}
                    <span className="font-mono">{apiError.correlationId}</span>
                  </>
                ) : (
                  "Nothing was set up. Check the details and try again."
                )}
              </Alert>
            )}
            {step === 0 && <BusinessStep {...stepProps} />}
            {step === 1 && (
              <ReceptionistStep
                {...stepProps}
                greetingChoice={greetingChoice}
                setGreetingChoice={setGreetingChoice}
                customGreeting={customGreeting}
                setCustomGreeting={(text) => {
                  setCustomGreeting(text);
                  setErrors((previous) =>
                    previous.custom_greeting ? { ...previous, custom_greeting: undefined } : previous,
                  );
                }}
              />
            )}
            {step === 2 && <PhoneStep {...stepProps} phoneMode={phoneMode} setPhoneMode={setPhoneMode} />}
            {step === 3 && (
              <ReviewStep
                {...stepProps}
                phoneMode={phoneMode}
                greeting={greeting}
                goTo={(index) => setStep(index)}
              />
            )}
          </div>

          <div className="flex flex-col-reverse gap-3 border-t border-line bg-surface-2 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-8">
            {step > 0 ? (
              <Button
                variant="ghost"
                onClick={() => setStep((step - 1) as Step)}
                leadingIcon={<ArrowLeftIcon size={16} />}
                disabled={submitting}
              >
                Back
              </Button>
            ) : (
              <span className="hidden sm:block" />
            )}
            {step < LAST_STEP ? (
              <Button type="submit" size="lg" trailingIcon={<ArrowRightIcon size={16} />}>
                Continue
              </Button>
            ) : (
              <Button type="submit" size="lg" loading={submitting}>
                {submitting ? "Activating…" : "Activate receptionist"}
              </Button>
            )}
          </div>
        </form>

        <aside className="lg:sticky lg:top-24 lg:self-start">
          <WizardPreview values={values} phoneMode={phoneMode} greeting={greeting} />
        </aside>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Steps
// ---------------------------------------------------------------------------

function BusinessStep({ values, error, update }: StepProps) {
  const services = splitServices(values.services);
  return (
    <>
      <TextField
        label="Business name"
        value={values.business_name}
        onChange={(event) => update("business_name", event.target.value)}
        placeholder="Northside Dental"
        autoComplete="organization"
        error={error("business_name")}
      />
      <ChoiceGroup
        legend="Business type"
        name="business_type"
        value={values.business_type}
        onChange={(value) => update("business_type", value)}
        options={BUSINESS_TYPE_OPTIONS}
        error={error("business_type")}
      />
      <div>
        <TextAreaField
          label="Services you offer"
          rows={3}
          value={values.services}
          onChange={(event) => update("services", event.target.value)}
          placeholder="Cleanings, whitening, emergency visits"
          hint="Separate services with commas."
          error={error("services")}
        />
        {services.length > 0 && (
          <ul aria-label="Services" className="mt-3 flex flex-wrap gap-2">
            {services.map((service, index) => (
              <li key={`${service}-${index}`}>
                <Badge>{service}</Badge>
              </li>
            ))}
          </ul>
        )}
      </div>
      <TextField
        label="Business phone"
        type="tel"
        inputMode="tel"
        autoComplete="tel"
        value={values.contact_phone}
        onChange={(event) => update("contact_phone", event.target.value)}
        placeholder="(805) 555-0142"
        hint="We use this to contact you about your account."
        error={error("contact_phone")}
      />
    </>
  );
}

function ReceptionistStep({
  values,
  error,
  update,
  greetingChoice,
  setGreetingChoice,
  customGreeting,
  setCustomGreeting,
}: StepProps & {
  greetingChoice: GreetingChoice;
  setGreetingChoice: (choice: GreetingChoice) => void;
  customGreeting: string;
  setCustomGreeting: (text: string) => void;
}) {
  const name = values.business_name.trim() || "your business";
  const styleOptions: ReadonlyArray<ChoiceOption<GreetingStyle>> = GREETING_STYLES.map((style) => ({
    value: style.value,
    label: style.label,
    description: GREETING_STYLE_COPY[style.value].description,
  }));
  const presetLine = greetingPreset(values.greeting_style, values.business_name);
  const remaining = GREETING_MAX_LENGTH - customGreeting.trim().length;

  return (
    <>
      <ChoiceGroup
        legend="Greeting style"
        name="greeting_style"
        value={values.greeting_style}
        onChange={(value) => update("greeting_style", value)}
        options={styleOptions}
        columns={3}
        error={error("greeting_style")}
      />

      <div>
        <ChoiceGroup
          legend="Opening line"
          name="greeting_choice"
          value={greetingChoice}
          onChange={setGreetingChoice}
          columns={1}
          options={[
            {
              value: "auto",
              label: "Write it for me",
              description: `We'll write ${name}'s opening line from your details, in the style above.`,
              icon: <MicIcon size={18} />,
            },
            {
              value: "preset",
              label: "Use this ready-made line",
              description: `“${presetLine}”`,
              icon: <MessageIcon size={18} />,
            },
            {
              value: "custom",
              label: "Write my own",
              description: "Say it exactly the way you want it said.",
              icon: <HeadsetIcon size={18} />,
            },
          ]}
        />

        {greetingChoice === "custom" && (
          <div className="mt-4">
            <TextAreaField
              label="Your opening line"
              rows={3}
              value={customGreeting}
              maxLength={GREETING_MAX_LENGTH}
              onChange={(event) => setCustomGreeting(event.target.value)}
              placeholder={presetLine}
              hint={`Read aloud exactly as written. ${formatNumber(Math.max(0, remaining))} characters left.`}
              error={error("custom_greeting")}
            />
          </div>
        )}
      </div>

      <div>
        <TextField
          label="Operating hours"
          value={values.operating_hours}
          onChange={(event) => update("operating_hours", event.target.value)}
          placeholder="Mon-Fri 9-6, Sat 10-2, closed Sun"
          hint="Write them however you like. We'll turn them into a schedule."
          error={error("operating_hours")}
        />
        <div role="group" aria-label="Common hours" className="mt-2.5 flex flex-wrap gap-2">
          {HOURS_PRESETS.map((preset) => (
            <Chip key={preset} onClick={() => update("operating_hours", preset)}>
              {preset}
            </Chip>
          ))}
        </div>
      </div>

      <div>
        <TextAreaField
          label="Escalation rules"
          optional
          rows={3}
          value={values.escalation_rules}
          onChange={(event) => update("escalation_rules", event.target.value)}
          placeholder="Text me if a caller says it's an emergency"
          hint="When should the receptionist flag a call as urgent or get you involved?"
          error={error("escalation_rules")}
        />
        <div role="group" aria-label="Suggested rules" className="mt-2.5 flex flex-wrap gap-2">
          {ESCALATION_SUGGESTIONS.map((suggestion) => (
            <Chip
              key={suggestion}
              onClick={() =>
                update(
                  "escalation_rules",
                  values.escalation_rules.trim() ? `${values.escalation_rules.trim()}\n${suggestion}` : suggestion,
                )
              }
            >
              + {suggestion}
            </Chip>
          ))}
        </div>
      </div>

      <TextField
        label="Notification email"
        type="email"
        autoComplete="email"
        value={values.notification_email}
        onChange={(event) => update("notification_email", event.target.value)}
        placeholder="owner@yourbusiness.com"
        hint="Call summaries and urgent alerts are sent here."
        error={error("notification_email")}
      />
    </>
  );
}

function PhoneStep({
  values,
  error,
  update,
  phoneMode,
  setPhoneMode,
}: StepProps & { phoneMode: PhoneMode; setPhoneMode: (mode: PhoneMode) => void }) {
  return (
    <>
      <ChoiceGroup
        legend="How will callers reach your receptionist?"
        name="phone_mode"
        value={phoneMode}
        onChange={setPhoneMode}
        options={[
          {
            value: "new",
            label: "Get a new local number",
            description: "We reserve a number in the area code you choose. Share it anywhere.",
            icon: <HashIcon size={18} />,
          },
          {
            value: "forward",
            label: "Keep my existing number",
            description:
              "We set up a dedicated AI line and you forward your current number to it. Callers keep dialing the number they know.",
            icon: <PhoneForwardIcon size={18} />,
          },
        ]}
      />
      <TextField
        label="Area code"
        inputMode="numeric"
        autoComplete="off"
        maxLength={3}
        value={values.area_code}
        onChange={(event) => update("area_code", event.target.value.replace(/\D/g, "").slice(0, 3))}
        placeholder="805"
        hint={
          phoneMode === "new"
            ? "Your new number will be local to this area code."
            : "Use your existing number's area code so the AI line is local to it."
        }
        error={error("area_code")}
        className="max-w-xs"
      />
      <div className="rounded-xl border border-line bg-surface-2 px-5 py-4">
        <p className="text-sm font-semibold">
          {phoneMode === "new" ? "Nothing is bought until your plan is confirmed" : "What happens next"}
        </p>
        <p className="mt-1 text-sm leading-relaxed text-muted">
          {phoneMode === "new"
            ? "Your plan is checked before a number is reserved, so a setup that doesn't go through never buys one."
            : "Once your receptionist is live, you'll forward your existing number to the new AI line. The next screen shows you exactly how."}
        </p>
      </div>
    </>
  );
}

function ReviewStep({
  values,
  error,
  update,
  phoneMode,
  greeting,
  goTo,
}: StepProps & { phoneMode: PhoneMode; greeting: string; goTo: (step: Step) => void }) {
  const services = splitServices(values.services);
  const style = GREETING_STYLE_COPY[values.greeting_style].label;
  const planOptions: ReadonlyArray<ChoiceOption<Plan>> = PLAN_CATALOG.map((plan) => ({
    value: plan.id,
    label: plan.name,
    description: `${formatNumber(plan.includedMinutes)} minutes included`,
  }));

  return (
    <>
      <div className="divide-y divide-line overflow-hidden rounded-xl border border-line">
        <ReviewGroup
          title="Business"
          onEdit={() => goTo(0)}
          items={[
            ["Business name", values.business_name],
            ["Business type", businessTypeLabel(values.business_type)],
            ["Services", services.length ? formatList(services) : "—"],
            ["Business phone", values.contact_phone],
          ]}
        />
        <ReviewGroup
          title="Receptionist"
          onEdit={() => goTo(1)}
          items={[
            ["Greeting style", style],
            ["Opening line", greeting ? `“${greeting}”` : "Written for you during setup"],
            ["Operating hours", values.operating_hours],
            ["Escalation rules", values.escalation_rules.trim() || "Take a message for anything it can't handle"],
            ["Notification email", values.notification_email],
          ]}
        />
        <ReviewGroup
          title="Phone"
          onEdit={() => goTo(2)}
          items={[
            [
              "Number",
              phoneMode === "new"
                ? `A new local number in area code ${values.area_code}`
                : `A new AI line in area code ${values.area_code}, with your existing number forwarded to it`,
            ],
          ]}
        />
      </div>

      <ChoiceGroup
        legend="Plan"
        name="plan"
        value={values.plan}
        onChange={(value) => update("plan", value)}
        options={planOptions}
        columns={3}
        error={error("plan")}
      />

      <div className="rounded-xl border border-accent-line bg-accent-soft px-5 py-5">
        <p className="text-sm font-semibold text-accent-ink">What we&apos;ll set up</p>
        <ul className="mt-3 space-y-2.5 text-sm text-ink-2">
          <SetupItem>A local phone number in area code {values.area_code}</SetupItem>
          <SetupItem>
            {greeting ? (
              <>
                An AI receptionist that opens with{" "}
                <span className="font-semibold">“{greeting}”</span>, in a {style.toLowerCase()} voice
              </>
            ) : (
              <>
                An AI receptionist with a {style.toLowerCase()} greeting, set up from your services and
                hours
              </>
            )}
          </SetupItem>
          <SetupItem>Call routing from that number to your receptionist</SetupItem>
          <SetupItem>
            Call summaries and urgent alerts sent to{" "}
            <span className="font-semibold break-all">{values.notification_email}</span>
          </SetupItem>
        </ul>
        <p className="mt-4 text-xs leading-relaxed text-accent-ink">
          Your plan is confirmed before a number is reserved, and you&apos;ll see every step as it happens.
        </p>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

function Chip({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-ink-2 transition-colors hover:border-line-strong hover:bg-surface-2"
    >
      {children}
    </button>
  );
}

function ReviewGroup({
  title,
  onEdit,
  items,
}: {
  title: string;
  onEdit: () => void;
  items: ReadonlyArray<readonly [string, string]>;
}) {
  return (
    <div className="px-4 py-4 sm:px-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-bold">{title}</p>
        <Button variant="ghost" size="sm" onClick={onEdit} aria-label={`Edit ${title.toLowerCase()} details`}>
          Edit
        </Button>
      </div>
      <dl className="mt-2 grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        {items.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="mt-0.5 font-medium break-words text-ink">{value || "—"}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function SetupItem({ children }: { children: ReactNode }) {
  return (
    <li className="flex gap-2.5">
      <CheckIcon size={16} strokeWidth={2.4} className="mt-0.5 shrink-0 text-accent" />
      <span className="min-w-0">{children}</span>
    </li>
  );
}

function WizardPreview({
  values,
  phoneMode,
  greeting,
}: {
  values: SignupRequest;
  phoneMode: PhoneMode;
  /** The chosen line, or empty when setup will write one. */
  greeting: string;
}) {
  const name = values.business_name.trim();
  const shown = greeting || GREETING_STYLE_COPY[values.greeting_style].sample(name || "your business");
  const checklist = [
    { label: "Business details", done: Boolean(name && splitServices(values.services).length) },
    { label: "Hours and greeting", done: Boolean(values.operating_hours.trim()) },
    { label: "Notifications", done: EMAIL.test(values.notification_email.trim()) },
    { label: "Phone number", done: /^\d{3}$/.test(values.area_code) },
  ];

  return (
    <div className="space-y-4">
      <div className="overflow-hidden rounded-2xl border border-night-line bg-night text-night-ink shadow-lg">
        <div className="flex items-center justify-between px-5 pt-5">
          <span className="flex items-center gap-2 text-xs font-semibold text-night-muted">
            <span className="size-1.5 rounded-full bg-emerald-400" />
            Incoming call
          </span>
          <span className="rounded-full border border-night-line px-2 py-0.5 text-[0.6875rem] text-night-muted">
            Preview
          </span>
        </div>
        <div className="px-5 pt-4 pb-5">
          <p className="text-xs text-night-muted">
            {phoneMode === "new" ? "Your new number" : "Forwarded to your AI line"}
          </p>
          <p className="mt-1 text-lg font-semibold tabular-nums">
            {values.area_code.length === 3 ? `+1 (${values.area_code}) ··· ····` : "+1 (···) ··· ····"}
          </p>
          <div className="mt-5 flex items-center gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-[#3651f0] text-white">
              <HeadsetIcon size={18} />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold">AI Receptionist</p>
              <p className="truncate text-xs text-night-muted">
                {name ? `Answering for ${name}` : "Answering for your business"}
              </p>
            </div>
            <span aria-hidden className="waveform ml-auto h-6 text-[#a3b0ff]">
              {[8, 16, 22, 12, 18, 10].map((height, index) => (
                <span key={index} style={{ height }} />
              ))}
            </span>
          </div>
          <p className="mt-4 rounded-xl bg-night-3 px-4 py-3 text-sm leading-relaxed">“{shown}”</p>
          <p className="mt-3 text-xs text-night-muted">
            {greeting
              ? "This is exactly what callers will hear."
              : "Your final greeting is written from your details during setup."}
          </p>
        </div>
      </div>

      <div className="card p-5">
        <p className="text-sm font-semibold">Your setup</p>
        <ul className="mt-3 space-y-2.5">
          {checklist.map((item) => (
            <li key={item.label} className="flex items-center gap-2.5 text-sm">
              <span
                className={cx(
                  "flex size-5 shrink-0 items-center justify-center rounded-full transition-colors",
                  item.done ? "bg-success text-bg" : "border border-line-strong",
                )}
              >
                {item.done && <CheckIcon size={12} strokeWidth={3} />}
              </span>
              <span className={item.done ? "text-ink" : "text-muted"}>{item.label}</span>
              <span className="sr-only">{item.done ? "(complete)" : "(not yet complete)"}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
