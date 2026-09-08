/**
 * The shapes the API returns.
 *
 * Hand-written rather than generated, because there are six of them and a
 * generator would be one more thing to run. They mirror `app/schemas/views.py`
 * and `app/schemas/signup.py`; the round-trip is covered by the backend tests.
 */

export const BUSINESS_TYPES = [
  { value: "salon", label: "Salon" },
  { value: "legal", label: "Legal" },
  { value: "medical", label: "Medical" },
  { value: "real_estate", label: "Real estate" },
  { value: "other", label: "Other" },
] as const;

export const GREETING_STYLES = [
  { value: "professional", label: "Professional" },
  { value: "friendly", label: "Friendly" },
  { value: "formal", label: "Formal" },
] as const;

export const PLANS = [
  { value: "starter", label: "Starter" },
  { value: "pro", label: "Pro" },
  { value: "enterprise", label: "Enterprise" },
] as const;

export type BusinessType = (typeof BUSINESS_TYPES)[number]["value"];
export type GreetingStyle = (typeof GREETING_STYLES)[number]["value"];
export type Plan = (typeof PLANS)[number]["value"];

export interface SignupRequest {
  business_name: string;
  business_type: BusinessType;
  services: string;
  operating_hours: string;
  greeting_style: GreetingStyle;
  escalation_rules: string;
  notification_email: string;
  area_code: string;
  plan: Plan;
  contact_phone: string;
}

export interface SignupResponse {
  tenant_id: string;
  run_id: string;
  tenant_status: string;
  provisioning_status: string;
  correlation_id: string;
  business_name: string;
  contact_email: string;
  timezone: string;
  /** False when an existing live signup was returned instead of a new one. */
  created: boolean;
}

/** Mirrors `StepStatus` on the backend. */
export type StepStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

export interface StepView {
  step_name: string;
  status: StepStatus;
  attempt: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

/** Mirrors `ProvisioningStatus`. */
export type ProvisioningStatus =
  | "draft"
  | "validated"
  | "config_generated"
  | "number_purchased"
  | "agent_created"
  | "number_linked"
  | "verified"
  | "active"
  | "failed"
  | "compensating"
  | "compensated";

export interface ProvisioningView {
  run_id: string;
  tenant_id: string;
  status: ProvisioningStatus;
  current_step: string | null;
  attempt: number;
  last_error: string | null;
  correlation_id: string;
  next_attempt_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  steps: StepView[];
  completed_steps: number;
  total_steps: number;
}

export interface TenantView {
  id: string;
  name: string;
  business_type: string;
  status: string;
  plan: string;
  timezone: string;
  contact_email: string;
  area_code: string | null;
  created_at: string;
}

export interface PhoneNumberView {
  e164: string;
  status: string;
  area_code: string | null;
  purchased_at: string | null;
  released_at: string | null;
}

export interface CallView {
  id: string;
  provider_call_id: string;
  direction: string;
  from_e164: string | null;
  started_at: string | null;
  duration_s: number | null;
  status: string;
  summary: string | null;
  caller_name: string | null;
  callback_number: string | null;
  intent: string | null;
  urgency: number | null;
}

/** The error envelope every backend failure uses. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    details?: Record<string, unknown>;
  };
  correlation_id: string | null;
}

/** Statuses from which nothing further will happen without intervention. */
export const TERMINAL_STATUSES: ReadonlySet<ProvisioningStatus> = new Set([
  "active",
  "failed",
  "compensated",
]);

export function isTerminal(status: ProvisioningStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** Human labels for the seven steps, in order. */
export const STEP_LABELS: Record<string, string> = {
  validate: "Validate the details",
  generate_config: "Write the receptionist's instructions",
  purchase_number: "Buy a phone number",
  create_agent: "Create the voice agent",
  link_number: "Connect the number to the agent",
  verify: "Verify the connection",
  activate: "Activate and send the welcome email",
};
