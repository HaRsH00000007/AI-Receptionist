/**
 * The only place that talks to FastAPI.
 *
 * Every failure comes back as an `ApiError` carrying the backend's own error
 * code, message and correlation id — so the UI can show the customer something
 * useful and a support conversation can start with an id rather than "it broke".
 */

import type {
  ApiErrorBody,
  CallView,
  PhoneNumberView,
  ProvisioningView,
  SignupRequest,
  SignupResponse,
  TenantView,
} from "./types";

/** Convenience alias so callers and tests name the same thing. */
export type CallsResult = CallView[];

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string | null;
  readonly fieldErrors: Record<string, string>;

  constructor(
    message: string,
    options: {
      status: number;
      code?: string;
      correlationId?: string | null;
      fieldErrors?: Record<string, string>;
    },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code ?? "unknown";
    this.correlationId = options.correlationId ?? null;
    this.fieldErrors = options.fieldErrors ?? {};
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
    throw new ApiError(
      "Could not reach the server. Is the API running?",
      { status: 0, code: "network_error" },
    );
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const body = payload as ApiErrorBody | null;
    if (body && typeof body === "object" && "error" in body) {
      throw new ApiError(body.error.message, {
        status: response.status,
        code: body.error.code,
        correlationId: body.correlation_id,
        fieldErrors: fieldErrorsFrom(body),
      });
    }
    throw new ApiError(`Request failed (${response.status})`, {
      status: response.status,
    });
  }

  return payload as T;
}

export function submitSignup(payload: SignupRequest): Promise<SignupResponse> {
  return request<SignupResponse>("/api/v1/signups", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/**
 * Tenant reads require a credential. The status page has no logged-in user --
 * the business has just submitted a form -- so it carries the signed, expiring
 * grant the signup response returned. A bare tenant id opens nothing.
 */
function withGrant(path: string, statusToken?: string): string {
  if (!statusToken) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}status_token=${encodeURIComponent(statusToken)}`;
}

export function getTenant(
  tenantId: string,
  statusToken?: string,
): Promise<TenantView> {
  return request<TenantView>(withGrant(`/api/v1/tenants/${tenantId}`, statusToken));
}

export function getProvisioning(
  tenantId: string,
  statusToken?: string,
): Promise<ProvisioningView> {
  return request<ProvisioningView>(
    withGrant(`/api/v1/tenants/${tenantId}/provisioning`, statusToken),
  );
}

export function getPhone(
  tenantId: string,
  statusToken?: string,
): Promise<PhoneNumberView> {
  return request<PhoneNumberView>(
    withGrant(`/api/v1/tenants/${tenantId}/phone`, statusToken),
  );
}

export function listCalls(
  tenantId: string,
  statusToken?: string,
): Promise<CallView[]> {
  return request<CallView[]>(
    withGrant(`/api/v1/tenants/${tenantId}/calls`, statusToken),
  );
}

/** A 404 is an expected answer for the number before it is bought. */
export async function getPhoneOrNull(
  tenantId: string,
  statusToken?: string,
): Promise<PhoneNumberView | null> {
  try {
    return await getPhone(tenantId, statusToken);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}
