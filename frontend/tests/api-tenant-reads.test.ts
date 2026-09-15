/**
 * Tenant reads from the dashboard.
 *
 * The dashboard runs on a different origin from the API, and `fetch` only
 * sends cookies cross-origin when asked. A tenant read without
 * `credentials: "include"` reaches the API with no session, gets a 401, and
 * signs the user straight back out — so the credential mode is pinned here.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, getPhone, getProfile, getProvisioning, getTenant, listCalls } from "@/lib/api";

function mockFetch(status: number, body: unknown) {
  const response = {
    ok: status >= 200 && status < 300,
    status,
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  } as Response;
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("tenant reads", () => {
  it("send the session cookie", async () => {
    const fetchMock = mockFetch(200, {});

    await getTenant("t1");
    await getProvisioning("t1");
    await getPhone("t1");
    await listCalls("t1");
    await getProfile("t1");

    expect(fetchMock).toHaveBeenCalledTimes(5);
    for (const [, init] of fetchMock.mock.calls) {
      expect((init as RequestInit).credentials).toBe("include");
    }
  });

  it("read the business profile with a status grant", async () => {
    const fetchMock = mockFetch(200, { services: [] });

    await getProfile("t1", "grant");

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toMatch(/\/api\/v1\/tenants\/t1\/profile\?status_token=grant$/);
  });

  it("combine a call limit with a status grant", async () => {
    const fetchMock = mockFetch(200, []);

    await listCalls("t1", "grant", { limit: 100 });

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toMatch(/\/calls\?limit=100&status_token=grant$/);
  });

  it("turn a non-JSON error page into an ApiError that keeps the status", async () => {
    mockFetch(502, "<html>Bad gateway</html>");

    const error = await getTenant("t1").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(502);
  });
});
