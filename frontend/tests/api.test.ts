/**
 * The API client's error handling.
 *
 * This is the layer that decides what a customer sees when something goes
 * wrong, so the mapping from the backend's error envelope to a field-level
 * message is worth pinning.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  getPhoneOrNull,
  getTenant,
  listCalls,
  submitSignup,
} from "@/lib/api";
import type { SignupRequest } from "@/lib/types";

const PAYLOAD: SignupRequest = {
  business_name: "Sunset Salon",
  business_type: "salon",
  services: "cuts, colour",
  operating_hours: "Mon-Fri 9-6",
  greeting_style: "friendly",
  custom_greeting: "",
  escalation_rules: "",
  notification_email: "owner@sunsetsalon.example.com",
  area_code: "805",
  plan: "starter",
  contact_phone: "8055550142",
};

function mockFetch(status: number, body: unknown) {
  const response = {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as Response;
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("submitSignup", () => {
  it("returns the created signup", async () => {
    mockFetch(201, {
      tenant_id: "t-1",
      run_id: "r-1",
      created: true,
      business_name: "Sunset Salon",
    });

    const result = await submitSignup(PAYLOAD);

    expect(result.tenant_id).toBe("t-1");
    expect(result.created).toBe(true);
  });

  it("surfaces per-field validation errors by name", async () => {
    mockFetch(422, {
      error: {
        code: "invalid_input",
        message: "phone number must have 10 digits",
        retryable: false,
        details: {
          field: "contact_phone",
          errors: [{ field: "contact_phone", message: "too short" }],
        },
      },
      correlation_id: "corr-123",
    });

    await expect(submitSignup(PAYLOAD)).rejects.toMatchObject({
      code: "invalid_input",
      correlationId: "corr-123",
      fieldErrors: { contact_phone: "too short" },
    });
  });

  it("keeps the correlation id so support has something to go on", async () => {
    mockFetch(500, {
      error: { code: "internal_error", message: "An unexpected error occurred.", retryable: true },
      correlation_id: "corr-abc",
    });

    const error = await submitSignup(PAYLOAD).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).correlationId).toBe("corr-abc");
  });

  it("distinguishes an unreachable API from a rejected request", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("failed"));

    const error = await submitSignup(PAYLOAD).catch((caught: unknown) => caught);

    expect((error as ApiError).code).toBe("network_error");
    expect((error as ApiError).status).toBe(0);
  });

  it("treats a rate limit as a normal, reportable rejection", async () => {
    mockFetch(429, {
      error: {
        code: "rate_limited",
        message: "too many signups from this address",
        retryable: true,
      },
      correlation_id: null,
    });

    await expect(submitSignup(PAYLOAD)).rejects.toMatchObject({
      status: 429,
      code: "rate_limited",
    });
  });
});

describe("getPhoneOrNull", () => {
  it("returns null before the number is bought", async () => {
    mockFetch(404, {
      error: { code: "not_found", message: "tenant has no active number", retryable: false },
      correlation_id: null,
    });

    await expect(getPhoneOrNull("t-1")).resolves.toBeNull();
  });

  it("propagates anything that is not a 404", async () => {
    mockFetch(500, {
      error: { code: "internal_error", message: "boom", retryable: true },
      correlation_id: null,
    });

    await expect(getPhoneOrNull("t-1")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("status grants", () => {
  it("sends the grant so the tenant read is authorized", async () => {
    const fetchMock = mockFetch(200, { id: "tenant-1", name: "Sunset Salon" });

    await getTenant("tenant-1", "v1.abc.123.sig");

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain("status_token=v1.abc.123.sig");
  });

  it("url-encodes the grant", async () => {
    // A signature is base64url, but the encoding is the client's job to get
    // right rather than something to assume about the token's alphabet.
    const fetchMock = mockFetch(200, []);

    await listCalls("tenant-1", "a+b/c=d");

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain("status_token=a%2Bb%2Fc%3Dd");
  });

  it("omits the parameter entirely when there is no grant", async () => {
    // The dashboard authenticates with a session cookie instead, so an empty
    // status_token must not be sent -- the API would try to verify it and fail.
    const fetchMock = mockFetch(200, { id: "tenant-1" });

    await getTenant("tenant-1");

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).not.toContain("status_token");
  });
});
