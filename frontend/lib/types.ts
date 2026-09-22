/**
 * The shapes the API returns.
 *
 * Hand-written rather than generated. They mirror `app/schemas/views.py`,
 * `app/schemas/signup.py`, `app/schemas/auth.py` and `app/schemas/business.py`;
 * the round-trip is covered by the backend tests.
 */

export const BUSINESS_TYPES = [
  { value: "salon", label: "Salon & spa" },
  { value: "legal", label: "Law firm" },
  { value: "medical", label: "Medical practice" },
  { value: "real_estate", label: "Real estate" },
  { value: "other", label: "Something else" },
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

export function businessTypeLabel(value: string): string {
  return BUSINESS_TYPES.find((option) => option.value === value)?.label ?? value;
}

/** One number on offer at the vendor. Not reserved until it is bought. */
export interface AvailableNumberView {
  e164: string;
  area_code: string;
  locality: string | null;
  region: string | null;
}

export interface NumberSearchView {
  requested_area_code: string;
  /**
   * True when the numbers are in the area code that was asked for. When false
   * they are nearby alternatives, offered only because that code had none.
   */
  exact_match: boolean;
  /** `exact_area_code`, `same_state` or `none`. */
  strategy: string;
  numbers: AvailableNumberView[];
}

export interface SignupRequest {
  business_name: string;
  business_type: BusinessType;
  services: string;
  operating_hours: string;
  greeting_style: GreetingStyle;
  /**
   * The exact opening line callers hear, chosen from the offered greetings or
   * written by the owner. Empty means "write one for me" during setup.
   */
  custom_greeting: string;
  escalation_rules: string;
  notification_email: string;
  area_code: string;
  /**
   * The number picked from the ones offered, in E.164. Empty means "choose one
   * for me" during setup. It may be in a different area code than `area_code`:
   * when the requested code has none, nearby numbers are offered instead.
   */
  selected_number: string;
  plan: Plan;
  contact_phone: string;
  /**
   * Only for the anonymous signup API. The web form no longer sends one: the
   * account is created first (`registerAccount`), and the business form is
   * submitted as that account (`OnboardingRequest`, which has no password).
   */
  password?: string;
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
  /**
   * A signed, expiring grant for this tenant's status page.
   *
   * The business has no account yet, so this is what lets them watch
   * provisioning without logging in. It replaces the earlier arrangement where
   * the tenant UUID alone opened the page — a capability that never expired and
   * leaked through Referer headers, history and screenshots.
   */
  status_token: string;
  /**
   * True when this signup created a new account with the password just chosen,
   * and the response also set the session cookie. The form then continues to
   * the signed-in setup page instead of the anonymous status page.
   */
  signed_in?: boolean;
}

/** Mirrors `StepStatus` on the backend. */
export type StepStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";

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
  | "billing_authorized"
  | "number_purchased"
  | "agent_created"
  | "number_linked"
  | "verified"
  | "active"
  | "failed"
  /**
   * Parked, not failed. The tenant is not entitled to provisioning yet —
   * nothing is broken, and the run resumes the moment billing says yes.
   */
  | "billing_blocked"
  | "compensating"
  | "compensated";

/**
 * The minimum billing state the status page needs.
 *
 * `entitled` is computed server-side from the subscription row. The client
 * never asserts it — a browser that could claim "paid" would walk straight
 * through the money gate.
 */
export interface BillingView {
  entitled: boolean;
  plan: string | null;
  status: string | null;
  trial_ends_at: string | null;
  /** Why provisioning is blocked, when it is. A stable code, not a sentence. */
  reason: string | null;
}

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
  /** Present so a run parked on billing can explain itself. */
  billing: BillingView | null;
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

export type CallStatus = "received" | "transcribed" | "summarized" | "notified" | "failed";

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
  /** Read off the conversation by a model — claimed by the caller, never verified. */
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

// ---------------------------------------------------------------------------
// Business profile — what the receptionist knows
// ---------------------------------------------------------------------------

export type Weekday =
  | "monday"
  | "tuesday"
  | "wednesday"
  | "thursday"
  | "friday"
  | "saturday"
  | "sunday";

export const WEEKDAYS: readonly Weekday[] = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
];

export interface DaySchedule {
  day: Weekday;
  closed: boolean;
  /** 24-hour "HH:MM", local to the business. */
  opens_at: string | null;
  closes_at: string | null;
}

export interface BusinessHours {
  timezone: string;
  days: DaySchedule[];
}

export type EscalationMode = "take_message" | "notify_owner" | "transfer";

export interface EscalationRule {
  when: string;
  mode: EscalationMode;
}

export interface EscalationPolicy {
  default_mode: EscalationMode;
  rules: EscalationRule[];
  notify_email: string | null;
  notify_phone: string | null;
}

export interface BusinessProfileView {
  services: string[];
  /** Exactly what the business typed. Always present when submitted. */
  hours_raw: string | null;
  /** The structured reading, once one exists. */
  hours: BusinessHours | null;
  greeting_style: GreetingStyle;
  /**
   * The opening line the owner chose or wrote, or null when they asked us to
   * write one. Distinct from `greeting`, which is whatever is live right now.
   */
  custom_greeting: string | null;
  escalation_raw: string | null;
  escalation: EscalationPolicy | null;
  /** The live opening line callers hear, once a configuration is live. */
  greeting: string | null;
  config_version: number | null;
}

// ---------------------------------------------------------------------------
// Authentication — what the dashboard needs to know about the viewer
// ---------------------------------------------------------------------------

export type MembershipRole = "owner" | "admin" | "member";

export interface TenantMembership {
  tenant_id: string;
  name: string;
  role: MembershipRole;
}

/**
 * The signed-in user.
 *
 * `memberships` is the authorization surface the UI reads: it decides which
 * organizations appear in the switcher. It is *not* the authorization itself —
 * every tenant-scoped request is re-checked server-side against the session
 * cookie, because a browser that could assert its own membership would be
 * asserting its own access.
 */
export interface SessionView {
  user_id: string;
  email: string;
  full_name: string | null;
  is_platform_admin: boolean;
  active_tenant_id: string | null;
  /** Whether the account can sign in with a password. Never the hash. */
  has_password?: boolean;
  memberships: TenantMembership[];
  impersonated: boolean;
}

// ---------------------------------------------------------------------------
// Usage
// ---------------------------------------------------------------------------

export interface UsageView {
  period_start: string;
  period_end: string;
  call_count: number;
  call_minutes: number;
  included_minutes: number;
  percent_used: number;
  over_limit: boolean;
  /** True at 80% — a warning, before anything is enforced. */
  warning: boolean;
  /** Whether calls stop at the limit. Only the trial plan does. */
  blocks_on_overage: boolean;
  plan: string;
}

// ---------------------------------------------------------------------------
// Agent configuration
// ---------------------------------------------------------------------------

export interface AgentConfigView {
  version: number;
  is_live: boolean;
  generated_by: string;
  generator_detail: string | null;
  template_version: string | null;
  voice_id: string | null;
  created_at: string;
}

export interface AgentConfigDetailView extends AgentConfigView {
  system_prompt: string;
  first_message: string;
  model_params: Record<string, unknown>;
}

export interface AgentView {
  elevenlabs_agent_id: string | null;
  status: string;
  config_version: number | null;
  voice_id?: string | null;
  generated_by?: string | null;
  synced_at: string | null;
}

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

export interface RunSummaryView {
  run_id: string;
  tenant_id: string;
  business_name: string;
  status: ProvisioningStatus;
  current_step: string | null;
  attempt: number;
  last_error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ActionResult {
  ok: boolean;
  detail: string;
  run_id?: string | null;
}

export interface ReadinessCheck {
  ok: boolean;
  detail: string | null;
  critical: boolean;
}

/** `GET /readyz`. Returned with a 503 when a critical dependency is down. */
export interface ReadinessView {
  status: "ready" | "not_ready";
  checks: Record<string, ReadinessCheck>;
  degraded: string[];
}

// ---------------------------------------------------------------------------
// Customer portal — mirrors `app/schemas/portal.py`
// ---------------------------------------------------------------------------

/** The Home numbers. Only what is recorded; nothing here is estimated. */
export interface DashboardSummaryView {
  window_days: number;
  window_start: string;
  answered_calls: number;
  average_duration_s: number | null;
  urgent_calls: number;
  callbacks_requested: number;
  unique_callers: number;
  total_contacts: number;
}

export interface TranscriptTurnView {
  /** `agent` or `user`, as the voice vendor labels speakers. */
  role: string;
  message: string;
  time_in_call_s: number | null;
}

export interface CallDetailView extends CallView {
  to_e164: string | null;
  agent_config_version: number | null;
  transcript: TranscriptTurnView[];
  recording_available: boolean;
}

/** A caller, derived from call history. Not a CRM record. */
export interface ContactView {
  phone_e164: string;
  name: string | null;
  call_count: number;
  first_call_at: string | null;
  last_call_at: string | null;
  last_summary: string | null;
  last_intent: string | null;
  has_urgent: boolean;
}

export interface ConfigVersionView {
  version: number;
  is_live: boolean;
  generated_by: string;
  created_at: string;
}

export interface AgentSettingsUpdate {
  services: string;
  operating_hours: string;
  greeting_style: GreetingStyle;
  custom_greeting: string;
  escalation_rules: string;
}

export interface AgentUpdateResult {
  config_version: number;
  previous_version: number | null;
  generated_by: string;
  /** False when the new version is saved but the voice agent is not updated yet. */
  synced: boolean;
  detail: string;
}

export interface RetryResult {
  run_id: string;
  status: string;
  steps_reset: string[];
}

export type IntegrationAvailability = "available" | "configuration_required" | "coming_soon";
export type IntegrationStatus = "not_connected" | "connected" | "error";

export interface IntegrationView {
  id: string;
  name: string;
  vendor: string;
  category: string;
  description: string;
  icon: string;
  auth_type: string;
  capabilities: string[];
  availability: IntegrationAvailability;
  status: IntegrationStatus;
  account: string | null;
  connected_at: string | null;
  last_error: string | null;
}

export interface IntegrationListView {
  business_type: string;
  integrations: IntegrationView[];
}

export interface IntegrationCheckView {
  ok: boolean;
  window_start: string;
  window_end: string;
  busy_blocks: number;
}

export type SmsState =
  | "not_configured"
  | "compliance_required"
  | "draft"
  | "submitted"
  | "under_review"
  | "approved"
  | "rejected"
  | "enabled";

export const SMS_USE_CASES = [
  { value: "customer_care", label: "Customer care" },
  { value: "account_notification", label: "Account notifications" },
  { value: "appointment_reminders", label: "Appointment reminders" },
  { value: "marketing", label: "Marketing" },
  { value: "mixed", label: "Mixed" },
] as const;

export type SmsUseCase = (typeof SMS_USE_CASES)[number]["value"];
export type SmsBrandType = "standard" | "sole_proprietor";

/** What the customer fills in. Every field optional until submission. */
export interface SmsRegistrationInput {
  brand_type: SmsBrandType;
  legal_business_name: string | null;
  tax_id: string | null;
  website: string | null;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  region: string | null;
  postal_code: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  use_case: SmsUseCase | null;
  campaign_description: string | null;
  sample_messages: string[];
  opt_in_description: string | null;
}

export interface SmsRegistrationView extends SmsRegistrationInput {
  status: SmsState;
  country: string;
  rejection_reason: string | null;
  submitted_at: string | null;
  approved_at: string | null;
  enabled_at: string | null;
}

export interface SmsStateView {
  state: SmsState;
  phone_number: string | null;
  /** The one flag campaign controls obey. True only when enabled. */
  can_send: boolean;
  editable: boolean;
  registration: SmsRegistrationView | null;
}

// ---------------------------------------------------------------------------
// Onboarding — account first, then the business
// ---------------------------------------------------------------------------

/** The business-setup form, for a signed-in account. No password: it has one. */
export type OnboardingRequest = Omit<SignupRequest, "password">;

/** The unfinished form, as saved. `data` is whatever the form stored. */
export interface OnboardingDraftView {
  data: Record<string, unknown> | null;
  updated_at: string | null;
}
