/**
 * How backend states read to a business owner.
 *
 * The API speaks in state-machine vocabulary (`billing_blocked`,
 * `compensated`, `[vendor_error] …`). None of that should reach a customer
 * verbatim, and none of it should be translated twice in two different ways —
 * so every status the UI shows is mapped here, once.
 */

import { humanize } from "./format";
import type { Tone } from "./tone";
import type { ProvisioningStatus } from "./types";

export interface StatusMeta {
  label: string;
  tone: Tone;
  description: string;
}

// ---------------------------------------------------------------------------
// The receptionist as a whole
// ---------------------------------------------------------------------------

export type ReceptionistState = "live" | "setting_up" | "billing" | "attention" | "inactive";

export function receptionistState(
  tenantStatus: string,
  provisioning: ProvisioningStatus | null,
): ReceptionistState {
  if (tenantStatus === "cancelled" || tenantStatus === "abandoned") return "inactive";
  if (tenantStatus === "active") return "live";
  if (provisioning === "billing_blocked") return "billing";
  if (
    tenantStatus === "failed" ||
    provisioning === "failed" ||
    provisioning === "compensating" ||
    provisioning === "compensated"
  ) {
    return "attention";
  }
  return "setting_up";
}

export const RECEPTIONIST_STATE: Record<ReceptionistState, StatusMeta> = {
  live: {
    label: "Live",
    tone: "success",
    description: "Your receptionist is live and answering calls.",
  },
  setting_up: {
    label: "Setting up",
    tone: "accent",
    description: "We're setting up your receptionist. This usually takes a few minutes.",
  },
  billing: {
    label: "Waiting on billing",
    tone: "warn",
    description: "Setup is paused until your subscription is active.",
  },
  attention: {
    label: "Needs attention",
    tone: "danger",
    description: "Setup didn't finish. Our team can see exactly where it stopped.",
  },
  inactive: {
    label: "Inactive",
    tone: "neutral",
    description: "This receptionist is not answering calls.",
  },
};

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

const CALL_STATUS: Record<string, StatusMeta> = {
  received: {
    label: "Processing",
    tone: "neutral",
    description: "The call has ended and is being processed.",
  },
  transcribed: {
    label: "Processing",
    tone: "neutral",
    description: "The conversation is in and its summary is being written.",
  },
  summarized: {
    label: "Summarized",
    tone: "accent",
    description: "The summary is ready.",
  },
  notified: {
    label: "Summary sent",
    tone: "success",
    description: "The summary was sent to your notification email.",
  },
  failed: {
    label: "Needs review",
    tone: "danger",
    description: "This call couldn't be summarized automatically.",
  },
};

export function callStatusMeta(status: string): StatusMeta {
  return CALL_STATUS[status] ?? { label: humanize(status), tone: "neutral", description: "" };
}

/** The distinct statuses a filter can offer, in lifecycle order. */
export const CALL_STATUS_FILTERS = [
  { value: "notified", label: "Summary sent" },
  { value: "summarized", label: "Summarized" },
  { value: "processing", label: "Processing" },
  { value: "failed", label: "Needs review" },
] as const;

export function callMatchesStatusFilter(status: string, filter: string): boolean {
  if (!filter) return true;
  if (filter === "processing") return status === "received" || status === "transcribed";
  return status === filter;
}

/** Urgency is scored 1–5 from the conversation; 4 and above is worth a flag. */
export function isUrgent(urgency: number | null): boolean {
  return urgency !== null && urgency >= 4;
}

// ---------------------------------------------------------------------------
// Provisioning
// ---------------------------------------------------------------------------

export const STEP_COPY: Record<string, { label: string; description: string }> = {
  validate: {
    label: "Validating business",
    description: "Checking that your details are complete and consistent.",
  },
  generate_config: {
    label: "Generating receptionist configuration",
    description: "Turning your details into a greeting, instructions and answers to common questions.",
  },
  billing_gate: {
    label: "Confirming your plan",
    description: "Making sure your plan is active before a number is reserved.",
  },
  purchase_number: {
    label: "Provisioning phone number",
    description: "Reserving a local number in your area code.",
  },
  create_agent: {
    label: "Creating AI receptionist",
    description: "Setting up the voice and conversation agent.",
  },
  link_number: {
    label: "Connecting phone number",
    description: "Routing calls on your number to your receptionist.",
  },
  verify: {
    label: "Verifying configuration",
    description: "Checking that the number and the receptionist are connected.",
  },
  activate: {
    label: "Activating receptionist",
    description: "Going live and sending your welcome email.",
  },
};

export function stepLabel(stepName: string | null | undefined): string {
  if (!stepName) return "—";
  return STEP_COPY[stepName]?.label ?? humanize(stepName);
}

export const PROVISIONING_STATUS: Record<ProvisioningStatus, StatusMeta> = {
  draft: { label: "Queued", tone: "neutral", description: "Waiting to start." },
  validated: { label: "In progress", tone: "accent", description: "Details validated." },
  config_generated: { label: "In progress", tone: "accent", description: "Configuration written." },
  billing_authorized: { label: "In progress", tone: "accent", description: "Plan confirmed." },
  number_purchased: { label: "In progress", tone: "accent", description: "Number reserved." },
  agent_created: { label: "In progress", tone: "accent", description: "Receptionist created." },
  number_linked: { label: "In progress", tone: "accent", description: "Number connected." },
  verified: { label: "In progress", tone: "accent", description: "Connection verified." },
  active: { label: "Active", tone: "success", description: "Live and answering calls." },
  failed: { label: "Failed", tone: "danger", description: "Stopped on an error." },
  billing_blocked: { label: "Waiting on billing", tone: "warn", description: "Paused for billing." },
  compensating: { label: "Rolling back", tone: "warn", description: "Releasing resources." },
  compensated: { label: "Abandoned", tone: "danger", description: "Stopped and rolled back." },
};

/**
 * Reason codes from the billing gate, rendered for a person.
 *
 * The API returns a stable code rather than a sentence, so wording can change
 * here without a backend deploy — and an unrecognised code falls back to
 * something honest rather than showing the customer a raw identifier.
 */
const BILLING_REASONS: Record<string, string> = {
  no_active_subscription: "Your subscription is not active yet. Complete checkout to continue.",
  trial_expired: "Your free trial has ended. Add a payment method to continue setup.",
  subscription_not_entitled:
    "Your subscription is not active. Update your billing details to continue.",
};

export function billingReason(code: string | null | undefined): string {
  return (
    BILLING_REASONS[code ?? ""] ??
    "We need an active subscription before setting up your number."
  );
}

export interface FriendlyError {
  code: string | null;
  title: string;
  explanation: string;
  /** The original text, for a support conversation. Never a stack trace. */
  technical: string;
}

const ERROR_COPY: Record<string, { title: string; explanation: string }> = {
  no_numbers_available: {
    title: "No numbers were available in that area code",
    explanation:
      "Our phone provider had no local numbers left in the area code you chose. A nearby area code usually works.",
  },
  vendor_error: {
    title: "A partner service returned an error",
    explanation:
      "One of the telephone or voice services we rely on didn't respond as expected. Nothing was charged for the step that failed.",
  },
  retryable_error: {
    title: "A temporary problem interrupted setup",
    explanation: "A service failed briefly. The step is safe to retry without buying anything twice.",
  },
  terminal_error: {
    title: "Setup hit a problem it couldn't recover from",
    explanation: "This needs a person to look at it before setup can continue.",
  },
  configuration_error: {
    title: "Something is misconfigured on our side",
    explanation: "This is a problem with our setup, not with the details you entered.",
  },
  invalid_input: {
    title: "Some details couldn't be used",
    explanation: "Part of the information we received didn't pass validation.",
  },
  dry_run_blocked: {
    title: "This environment is in test mode",
    explanation: "Real phone numbers aren't purchased here, so setup stopped before buying one.",
  },
};

const ERROR_PATTERN = /^\[([a-z0-9_]+)\]\s*([\s\S]*)$/;

/** `[vendor_error] twilio returned 400` → a sentence a business owner can read. */
export function friendlyError(raw: string | null | undefined): FriendlyError {
  const technical = (raw ?? "").trim();
  const match = ERROR_PATTERN.exec(technical);
  const code = match?.[1] ?? null;
  const copy = (code && ERROR_COPY[code]) || {
    title: "Setup didn't finish",
    explanation: "Something went wrong while setting up your receptionist.",
  };
  return { code, technical, ...copy };
}
