"use client";

/**
 * The signup form.
 *
 * Validation is deliberately thin here: the backend is the authority on what a
 * valid phone number or area code is, and duplicating those rules in TypeScript
 * would give two answers that drift apart. The form checks only that required
 * fields are filled, submits, and renders whatever the API says — including
 * per-field errors, which the API returns by name.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, submitSignup } from "@/lib/api";
import {
  BUSINESS_TYPES,
  GREETING_STYLES,
  PLANS,
  type SignupRequest,
} from "@/lib/types";

const EMPTY: SignupRequest = {
  business_name: "",
  business_type: "salon",
  services: "",
  operating_hours: "",
  greeting_style: "professional",
  escalation_rules: "",
  notification_email: "",
  area_code: "",
  plan: "starter",
  contact_phone: "",
};

export function SignupForm() {
  const router = useRouter();
  const [values, setValues] = useState<SignupRequest>(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  function update<K extends keyof SignupRequest>(
    key: K,
    value: SignupRequest[K],
  ) {
    setValues((previous) => ({ ...previous, [key]: value }));
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await submitSignup(values);
      // The grant travels with the redirect; without it the status page
      // cannot read anything.
      router.push(
        `/status/${result.tenant_id}?t=${encodeURIComponent(result.status_token)}`,
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("Something went wrong.", { status: 0 }),
      );
      setSubmitting(false);
    }
  }

  function fieldError(name: keyof SignupRequest): string | undefined {
    return error?.fieldErrors[name];
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-7">
      <header className="space-y-3">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-accent-line bg-accent-soft px-3 py-1 text-xs font-medium text-accent">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
          Live in about two minutes
        </span>
        <h1 className="text-3xl font-semibold tracking-tight text-balance">
          Set up your AI receptionist
        </h1>
        <p className="max-w-xl text-sm leading-relaxed text-muted">
          Tell us about your business. We will buy you a phone number and have an
          AI answering it, using your own greeting, services and hours.
        </p>
      </header>

      {error && (
        <div
          role="alert"
          className="rounded-xl border border-danger-line bg-danger-soft px-4 py-3.5"
        >
          <p className="text-sm font-medium text-danger-ink">{error.message}</p>
          {error.correlationId && (
            <p className="mt-1.5 font-mono text-xs text-danger-ink/70">
              Reference: {error.correlationId}
            </p>
          )}
        </div>
      )}

      <section className="card space-y-6 p-6">
        <SectionTitle>The business</SectionTitle>

        <Field label="Business name" error={fieldError("business_name")}>
          <input
            className="field"
            value={values.business_name}
            onChange={(event) => update("business_name", event.target.value)}
            placeholder="Sunset Salon"
            required
            aria-label="Business name"
          />
        </Field>

        <div className="grid gap-6 sm:grid-cols-2">
          <Field label="Business type" error={fieldError("business_type")}>
            <select
              className="field"
              value={values.business_type}
              onChange={(event) =>
                update(
                  "business_type",
                  event.target.value as SignupRequest["business_type"],
                )
              }
              aria-label="Business type"
            >
              {BUSINESS_TYPES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Plan" error={fieldError("plan")}>
            <select
              className="field"
              value={values.plan}
              onChange={(event) =>
                update("plan", event.target.value as SignupRequest["plan"])
              }
              aria-label="Plan"
            >
              {PLANS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field
          label="Services offered"
          hint="Separate them with commas."
          error={fieldError("services")}
        >
          <input
            className="field"
            value={values.services}
            onChange={(event) => update("services", event.target.value)}
            placeholder="cuts, colour, walk-ins"
            required
            aria-label="Services offered"
          />
        </Field>

        <Field
          label="Operating hours"
          hint="Write them however you like — we will read them."
          error={fieldError("operating_hours")}
        >
          <input
            className="field"
            value={values.operating_hours}
            onChange={(event) => update("operating_hours", event.target.value)}
            placeholder="Mon-Fri 9-6, Sat till 2, closed Sun"
            required
            aria-label="Operating hours"
          />
        </Field>
      </section>

      <section className="card space-y-6 p-6">
        <SectionTitle>How it should answer</SectionTitle>

        <div className="grid gap-6 sm:grid-cols-2">
          <Field label="Greeting style" error={fieldError("greeting_style")}>
            <select
              className="field"
              value={values.greeting_style}
              onChange={(event) =>
                update(
                  "greeting_style",
                  event.target.value as SignupRequest["greeting_style"],
                )
              }
              aria-label="Greeting style"
            >
              {GREETING_STYLES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label="Preferred area code"
            hint="We will try this first."
            error={fieldError("area_code")}
          >
            <input
              className="field"
              value={values.area_code}
              onChange={(event) => update("area_code", event.target.value)}
              placeholder="805"
              inputMode="numeric"
              required
              aria-label="Preferred area code"
            />
          </Field>
        </div>

        <Field
          label="Escalation rules"
          hint="When should we interrupt you? Optional."
          error={fieldError("escalation_rules")}
        >
          <textarea
            className="field resize-y"
            rows={2}
            value={values.escalation_rules}
            onChange={(event) => update("escalation_rules", event.target.value)}
            placeholder="Text me if a caller says it is an emergency"
            aria-label="Escalation rules"
          />
        </Field>
      </section>

      <section className="card space-y-6 p-6">
        <SectionTitle>Where to reach you</SectionTitle>

        <div className="grid gap-6 sm:grid-cols-2">
          <Field label="Notification email" error={fieldError("notification_email")}>
            <input
              className="field"
              type="email"
              value={values.notification_email}
              onChange={(event) => update("notification_email", event.target.value)}
              placeholder="owner@yourbusiness.com"
              required
              aria-label="Notification email"
            />
          </Field>

          <Field label="Contact phone" error={fieldError("contact_phone")}>
            <input
              className="field"
              value={values.contact_phone}
              onChange={(event) => update("contact_phone", event.target.value)}
              placeholder="(805) 555-0142"
              required
              aria-label="Contact phone"
            />
          </Field>
        </div>
      </section>

      <div className="space-y-3">
        <button type="submit" disabled={submitting} className="btn w-full">
          {submitting && <Spinner />}
          {submitting ? "Setting things up…" : "Create my receptionist"}
        </button>
        <p className="text-center text-xs text-muted">
          You will see each step as it happens, and nothing is charged if setup
          does not finish.
        </p>
      </div>
    </form>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-xs font-semibold tracking-[0.08em] text-muted uppercase">
      {children}
    </h2>
  );
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      {hint && <span className="ml-2 text-xs text-muted">{hint}</span>}
      <div className="mt-1.5">{children}</div>
      {error && <p className="mt-1.5 text-xs text-danger">{error}</p>}
    </label>
  );
}

function Spinner() {
  return (
    <svg
      className="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}
