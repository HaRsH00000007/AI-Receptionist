/**
 * The only place that talks to FastAPI.
 *
 * Every failure comes back as an `ApiError` carrying the backend's own error
 * code, message and correlation id — so the UI can show the customer something
 * useful and a support conversation can start with an id rather than "it broke".
 */

import type {
  ActionResult,
  AgentConfigDetailView,
  AgentConfigView,
  AgentSettingsUpdate,
  AgentUpdateResult,
  AgentView,
  ApiErrorBody,
  BusinessProfileView,
  CallDetailView,
  CallView,
  ConfigVersionView,
  ContactView,
  DashboardSummaryView,
  IntegrationCheckView,
  IntegrationListView,
  IntegrationView,
  NumberSearchView,
  OnboardingDraftView,
  OnboardingRequest,
  PhoneNumberView,
  ProvisioningView,
  ReadinessView,
  RetryResult,
  RunSummaryView,
  SessionView,
  SignupRequest,
  SignupResponse,
  SmsRegistrationInput,
  SmsStateView,
  TenantView,
  UsageView,
} from "./types";

/** Convenience alias so callers and tests name the same thing. */
export type CallsResult = CallView[];

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string | null;
  readonly fieldErrors: Record<string, string>;
  /** The error envelope's structured `details`, when the server sent any. */
  readonly details: Record<string, unknown>;

  constructor(
    message: string,
    options: {
      status: number;
      code?: string;
      correlationId?: string | null;
      fieldErrors?: Record<string, string>;
      details?: Record<string, unknown>;
    },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code ?? "unknown";
    this.correlationId = options.correlationId ?? null;
    this.fieldErrors = options.fieldErrors ?? {};
    this.details = options.details ?? {};
  }
}

function fieldErrorsFrom(body: ApiErrorBody): Record<string, string> {
  const details = body.error.details;
  if (!details) return {};

  const collected: Record<string, string> = {};
  const errors = details["errors"];
  if (Array.isArray(errors)) {
    for (const entry of errors) {
      if (
        entry &&
        typeof entry === "object" &&
        typeof (entry as Record<string, unknown>)["field"] === "string"
      ) {
        const record = entry as Record<string, unknown>;
        collected[record["field"] as string] = String(record["message"] ?? "");
      }
    }
  }
  // A single-field rejection (a normalizer, say) names the field directly.
  if (typeof details["field"] === "string") {
    collected[details["field"]] ??= body.error.message;
  }
  return collected;
}

function parseJson(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    // A proxy's HTML error page, say. Treated as "no body" so the caller gets an
    // ApiError with the status rather than a SyntaxError with no context.
    return null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    // The API is unreachable — a different problem from an API that said no,
    // and worth a different message.
    throw new ApiError("Could not reach the server. Is the API running?", {
      status: 0,
      code: "network_error",
    });
  }

  if (response.status === 204) return undefined as T;

  const payload = parseJson(await response.text());

  if (!response.ok) {
    const body = payload as ApiErrorBody | null;
    if (body && typeof body === "object" && "error" in body) {
      throw new ApiError(body.error.message, {
        status: response.status,
        code: body.error.code,
        correlationId: body.correlation_id,
        fieldErrors: fieldErrorsFrom(body),
        details: body.error.details,
      });
    }
    throw new ApiError(`Request failed (${response.status})`, {
      status: response.status,
    });
  }

  return payload as T;
}

/**
 * Session requests must carry the cookie.
 *
 * The session lives in an HttpOnly cookie, so JavaScript cannot read it — which
 * is the point: an XSS bug cannot exfiltrate a session it cannot see. The
 * trade-off is that `fetch` will not send it cross-origin unless asked, hence
 * `credentials: "include"` on every authenticated call.
 */
const withCredentials: RequestInit = { credentials: "include" };

/** Swallow a 404, which several reads use to mean "not created yet". */
async function orNull<T>(pending: Promise<T>): Promise<T | null> {
  try {
    return await pending;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export function submitSignup(payload: SignupRequest): Promise<SignupResponse> {
  // Credentials, because a new account is signed in by this response: the
  // session arrives as an HttpOnly cookie, which a cross-origin browser only
  // stores when the request was allowed to carry credentials.
  return request<SignupResponse>("/api/v1/signups", {
    ...withCredentials,
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/**
 * Numbers the business can choose from.
 *
 * Searching reserves nothing and costs nothing, so the form can call this as
 * often as someone retypes an area code. When the requested code has no
 * inventory the answer carries nearby alternatives instead, with
 * `exact_match: false` — that is the only time alternatives appear.
 */
export function searchAvailableNumbers(areaCode: string): Promise<NumberSearchView> {
  return request<NumberSearchView>(
    `/api/v1/numbers/available?area_code=${encodeURIComponent(areaCode)}`,
  );
}

// ---------------------------------------------------------------------------
// Tenant-scoped reads
//
// Two credentials reach these. The post-signup status page has no account, so
// it carries the signed, expiring grant the signup response returned. The
// dashboard has a session cookie and passes no token. Either way the cookie is
// sent — a bare tenant id opens nothing, and the server decides.
// ---------------------------------------------------------------------------

function withGrant(path: string, statusToken?: string): string {
  if (!statusToken) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}status_token=${encodeURIComponent(statusToken)}`;
}

function tenantRead<T>(path: string, statusToken?: string): Promise<T> {
  return request<T>(withGrant(path, statusToken), withCredentials);
}

export function getTenant(tenantId: string, statusToken?: string): Promise<TenantView> {
  return tenantRead<TenantView>(`/api/v1/tenants/${tenantId}`, statusToken);
}

export function getProvisioning(
  tenantId: string,
  statusToken?: string,
): Promise<ProvisioningView> {
  return tenantRead<ProvisioningView>(`/api/v1/tenants/${tenantId}/provisioning`, statusToken);
}

export function getPhone(tenantId: string, statusToken?: string): Promise<PhoneNumberView> {
  return tenantRead<PhoneNumberView>(`/api/v1/tenants/${tenantId}/phone`, statusToken);
}

/** A 404 is an expected answer for the number before it is bought. */
export function getPhoneOrNull(
  tenantId: string,
  statusToken?: string,
): Promise<PhoneNumberView | null> {
  return orNull(getPhone(tenantId, statusToken));
}

/** Newest first. The API caps `limit` at 100. */
export function listCalls(
  tenantId: string,
  statusToken?: string,
  options: { limit?: number; caller?: string } = {},
): Promise<CallView[]> {
  const params = new URLSearchParams();
  if (options.limit) params.set("limit", String(options.limit));
  // One caller's history, for a contact. The server filters, so the result is
  // their calls rather than whichever of them fell inside the latest 100.
  if (options.caller) params.set("caller", options.caller);
  const query = params.size ? `?${params.toString()}` : "";
  return tenantRead<CallView[]>(`/api/v1/tenants/${tenantId}/calls${query}`, statusToken);
}

export function getProfile(tenantId: string, statusToken?: string): Promise<BusinessProfileView> {
  return tenantRead<BusinessProfileView>(`/api/v1/tenants/${tenantId}/profile`, statusToken);
}

/** A 404 means an API that predates the profile endpoint, or no profile yet. */
export function getProfileOrNull(
  tenantId: string,
  statusToken?: string,
): Promise<BusinessProfileView | null> {
  return orNull(getProfile(tenantId, statusToken));
}

export function getUsage(tenantId: string): Promise<UsageView> {
  return tenantRead<UsageView>(`/api/v1/tenants/${tenantId}/usage`);
}

export function getAgent(tenantId: string): Promise<AgentView> {
  return tenantRead<AgentView>(`/api/v1/tenants/${tenantId}/agent`);
}

/** A 404 is expected before the agent is created. */
export function getAgentOrNull(tenantId: string): Promise<AgentView | null> {
  return orNull(getAgent(tenantId));
}

// ---------------------------------------------------------------------------
// The signed-in portal
//
// Membership-only on the server: none of these accept the status grant, so
// none take a `statusToken`. Writes need an owner or admin; the API answers a
// plain member with 403, which the pages avoid by not offering the control.
// ---------------------------------------------------------------------------

function portal<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>(`/api/v1/tenants/${path}`, { ...withCredentials, ...init });
}

function send<T>(path: string, method: "POST" | "PUT" | "PATCH", body?: unknown): Promise<T> {
  return portal<T>(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function getDashboardSummary(tenantId: string, days = 30): Promise<DashboardSummaryView> {
  return portal<DashboardSummaryView>(`${tenantId}/dashboard?days=${days}`);
}

export function getCallDetail(tenantId: string, callId: string): Promise<CallDetailView> {
  return portal<CallDetailView>(`${tenantId}/calls/${callId}`);
}

export function listContacts(tenantId: string): Promise<ContactView[]> {
  return portal<ContactView[]>(`${tenantId}/contacts`);
}

export function listConfigVersions(tenantId: string): Promise<ConfigVersionView[]> {
  return portal<ConfigVersionView[]>(`${tenantId}/agent/versions`);
}

export function updateAgentSettings(
  tenantId: string,
  settings: AgentSettingsUpdate,
): Promise<AgentUpdateResult> {
  return send<AgentUpdateResult>(`${tenantId}/agent/settings`, "PUT", settings);
}

export function retryProvisioning(tenantId: string): Promise<RetryResult> {
  return send<RetryResult>(`${tenantId}/provisioning/retry`, "POST");
}

export function listIntegrations(tenantId: string): Promise<IntegrationListView> {
  return portal<IntegrationListView>(`${tenantId}/integrations`);
}

/** Returns the provider's consent URL. Nothing is connected until it returns. */
export function connectIntegration(
  tenantId: string,
  provider: string,
): Promise<{ authorization_url: string }> {
  return send<{ authorization_url: string }>(
    `${tenantId}/integrations/${encodeURIComponent(provider)}/connect`,
    "POST",
  );
}

export function disconnectIntegration(tenantId: string, provider: string): Promise<IntegrationView> {
  return send<IntegrationView>(
    `${tenantId}/integrations/${encodeURIComponent(provider)}/disconnect`,
    "POST",
  );
}

export function verifyIntegration(
  tenantId: string,
  provider: string,
): Promise<IntegrationCheckView> {
  return send<IntegrationCheckView>(
    `${tenantId}/integrations/${encodeURIComponent(provider)}/verify`,
    "POST",
  );
}

export function getSmsState(tenantId: string): Promise<SmsStateView> {
  return portal<SmsStateView>(`${tenantId}/sms`);
}

export function saveSmsRegistration(
  tenantId: string,
  registration: SmsRegistrationInput,
): Promise<SmsStateView> {
  return send<SmsStateView>(`${tenantId}/sms/registration`, "PUT", registration);
}

export function submitSmsRegistration(tenantId: string): Promise<SmsStateView> {
  return send<SmsStateView>(`${tenantId}/sms/registration/submit`, "POST");
}

// ---------------------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------------------

export function requestMagicLink(email: string): Promise<{ message: string }> {
  return request<{ message: string }>("/api/v1/auth/magic-link", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

/**
 * Sign in with a password.
 *
 * `withCredentials` matters: the session arrives as an HttpOnly cookie the
 * browser will only store if the request was allowed to carry credentials.
 * Without it the call appears to succeed and every later request is a 401.
 */
export function signInWithPassword(email: string, password: string): Promise<SessionView> {
  return request<SessionView>("/api/v1/auth/password-session", {
    ...withCredentials,
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function exchangeMagicLink(token: string): Promise<SessionView> {
  return request<SessionView>("/api/v1/auth/session", {
    ...withCredentials,
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function getSession(): Promise<SessionView> {
  return request<SessionView>("/api/v1/auth/me", withCredentials);
}

/** A 401 is the expected answer for a signed-out visitor, not an error. */
export async function getSessionOrNull(): Promise<SessionView | null> {
  try {
    return await getSession();
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      return null;
    }
    throw error;
  }
}

/**
 * Create an account and sign in — the first step of getting started. The
 * session arrives as an HttpOnly cookie, hence credentials.
 */
export function registerAccount(
  fullName: string,
  email: string,
  password: string,
): Promise<SessionView> {
  return request<SessionView>("/api/v1/auth/register", {
    ...withCredentials,
    method: "POST",
    body: JSON.stringify({ full_name: fullName, email, password }),
  });
}

/** Create the business for the signed-in account. Its owner is the session. */
export function submitOnboarding(payload: OnboardingRequest): Promise<SignupResponse> {
  return request<SignupResponse>("/api/v1/onboarding", {
    ...withCredentials,
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getOnboardingDraft(): Promise<OnboardingDraftView> {
  return request<OnboardingDraftView>("/api/v1/onboarding/draft", withCredentials);
}

export function saveOnboardingDraft(data: Record<string, unknown>): Promise<OnboardingDraftView> {
  return request<OnboardingDraftView>("/api/v1/onboarding/draft", {
    ...withCredentials,
    method: "PUT",
    body: JSON.stringify({ data }),
  });
}

export function updateAccount(fullName: string): Promise<SessionView> {
  return request<SessionView>("/api/v1/auth/me", {
    ...withCredentials,
    method: "PATCH",
    body: JSON.stringify({ full_name: fullName }),
  });
}

/**
 * Set or change the password. `currentPassword` is required when the account
 * already has one; every other session is signed out on success.
 */
export function changePassword(currentPassword: string | null, newPassword: string): Promise<void> {
  return request<void>("/api/v1/auth/password", {
    ...withCredentials,
    method: "POST",
    body: JSON.stringify({
      ...(currentPassword ? { current_password: currentPassword } : {}),
      new_password: newPassword,
    }),
  });
}

export async function logout(): Promise<void> {
  await request<void>("/api/v1/auth/session", {
    ...withCredentials,
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Admin
//
// The admin key is supplied per request by the operator and is never persisted
// by this module. Keeping it out of localStorage means an XSS bug on the admin
// page cannot lift a credential that authorizes retries and releases.
// ---------------------------------------------------------------------------

function adminHeaders(adminKey: string): RequestInit {
  return { ...withCredentials, headers: { "x-admin-key": adminKey } };
}

export function listRuns(
  adminKey: string,
  options: { status?: string; limit?: number } = {},
): Promise<RunSummaryView[]> {
  const params = new URLSearchParams();
  if (options.status) params.set("status", options.status);
  if (options.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  return request<RunSummaryView[]>(
    `/api/v1/admin/runs${query ? `?${query}` : ""}`,
    adminHeaders(adminKey),
  );
}

export function retryRun(adminKey: string, runId: string): Promise<ActionResult> {
  return request<ActionResult>(`/api/v1/admin/runs/${runId}/retry`, {
    ...adminHeaders(adminKey),
    method: "POST",
  });
}

export function abandonRun(adminKey: string, runId: string): Promise<ActionResult> {
  return request<ActionResult>(`/api/v1/admin/runs/${runId}/abandon`, {
    ...adminHeaders(adminKey),
    method: "POST",
  });
}

export function resyncAgent(adminKey: string, tenantId: string): Promise<ActionResult> {
  return request<ActionResult>(`/api/v1/admin/tenants/${tenantId}/resync-agent`, {
    ...adminHeaders(adminKey),
    method: "POST",
  });
}

export function listConfigs(adminKey: string, tenantId: string): Promise<AgentConfigView[]> {
  return request<AgentConfigView[]>(
    `/api/v1/admin/tenants/${tenantId}/configs`,
    adminHeaders(adminKey),
  );
}

export function getConfigDetail(
  adminKey: string,
  tenantId: string,
  version: number,
): Promise<AgentConfigDetailView> {
  return request<AgentConfigDetailView>(
    `/api/v1/admin/tenants/${tenantId}/configs/${version}`,
    adminHeaders(adminKey),
  );
}

export function rollbackConfig(
  adminKey: string,
  tenantId: string,
  version: number,
): Promise<ActionResult> {
  return request<ActionResult>(`/api/v1/admin/tenants/${tenantId}/configs/${version}/rollback`, {
    ...adminHeaders(adminKey),
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Platform health
// ---------------------------------------------------------------------------

/**
 * `GET /readyz`. Read even when it answers 503 — a not-ready body is exactly
 * the information an operator came for, not a failure to display it.
 */
export async function getReadiness(): Promise<ReadinessView> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/readyz`, { cache: "no-store" });
  } catch {
    throw new ApiError("Could not reach the server.", { status: 0, code: "network_error" });
  }
  const body = parseJson(await response.text()) as ReadinessView | null;
  if (body && typeof body === "object" && "checks" in body) return body;
  throw new ApiError(`Readiness check failed (${response.status})`, { status: response.status });
}
