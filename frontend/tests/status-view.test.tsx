/**
 * The status page: progress, the live number, failure, and when polling stops.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StatusView } from "@/app/status/[tenantId]/StatusView";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { ProvisioningView, StepStatus, TenantView } from "@/lib/types";

const STEPS = [
  "validate",
  "generate_config",
  "purchase_number",
  "create_agent",
  "link_number",
  "verify",
  "activate",
];

const TENANT: TenantView = {
  id: "t-1",
  name: "Sunset Salon",
  business_type: "salon",
  status: "pending",
  plan: "starter",
  timezone: "America/Los_Angeles",
  contact_email: "owner@sunsetsalon.example.com",
  area_code: "805",
  created_at: "2026-09-06T00:00:00Z",
};

function provisioning(
  overrides: Partial<ProvisioningView> = {},
  statuses: StepStatus[] = STEPS.map(() => "pending"),
): ProvisioningView {
  return {
    run_id: "r-1",
    tenant_id: "t-1",
    status: "draft",
    current_step: null,
    attempt: 0,
    last_error: null,
    correlation_id: "corr-1",
    next_attempt_at: null,
    started_at: null,
    finished_at: null,
    steps: STEPS.map((name, index) => ({
      step_name: name,
      status: statuses[index] ?? "pending",
      attempt: 0,
      error: null,
      started_at: null,
      finished_at: null,
    })),
    completed_steps: statuses.filter((status) => status === "succeeded").length,
    total_steps: STEPS.length,
    ...overrides,
  };
}

function stub(view: ProvisioningView, phone: string | null = null, calls: api.CallsResult = []) {
  vi.spyOn(api, "getTenant").mockResolvedValue(TENANT);
  vi.spyOn(api, "getProvisioning").mockResolvedValue(view);
  vi.spyOn(api, "getPhoneOrNull").mockResolvedValue(
    phone
      ? { e164: phone, status: "active", area_code: "805", purchased_at: null, released_at: null }
      : null,
  );
  vi.spyOn(api, "listCalls").mockResolvedValue(calls);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("StatusView", () => {
  it("shows all seven steps from the first render", async () => {
    stub(provisioning());
    render(<StatusView tenantId="t-1" />);

    await screen.findByText("Sunset Salon");
    expect(screen.getByText("Buy a phone number")).toBeInTheDocument();
    expect(screen.getByText("Verify the connection")).toBeInTheDocument();
    expect(screen.getByText(/0 of 7/)).toBeInTheDocument();
  });

  it("shows the number once the run is active", async () => {
    stub(
      provisioning({ status: "active" }, STEPS.map(() => "succeeded" as StepStatus)),
      "+18055551000",
    );
    render(<StatusView tenantId="t-1" />);

    expect(await screen.findByText("+18055551000")).toBeInTheDocument();
    expect(screen.getByText(/Your receptionist is live/)).toBeInTheDocument();
    expect(screen.getByText(/7 of 7/)).toBeInTheDocument();
  });

  it("shows the failure and its reference without pretending nothing happened", async () => {
    stub(
      provisioning({
        status: "compensated",
        last_error: "[vendor_error] twilio returned 400",
      }),
    );
    render(<StatusView tenantId="t-1" />);

    await screen.findByRole("alert");
    expect(screen.getByText("Setup did not complete")).toBeInTheDocument();
    expect(screen.getByText("[vendor_error] twilio returned 400")).toBeInTheDocument();
    expect(screen.getByText(/corr-1/)).toBeInTheDocument();
  });

  it("shows a step's own error next to that step", async () => {
    const view = provisioning();
    view.steps[2] = {
      step_name: "purchase_number",
      status: "failed",
      attempt: 3,
      error: "[no_numbers_available] nothing in 805",
      started_at: null,
      finished_at: null,
    };
    stub(view);
    render(<StatusView tenantId="t-1" />);

    expect(
      await screen.findByText("[no_numbers_available] nothing in 805"),
    ).toBeInTheDocument();
    expect(screen.getByText("attempt 3")).toBeInTheDocument();
  });

  it("renders a call summary but never the transcript", async () => {
    stub(
      provisioning({ status: "active" }, STEPS.map(() => "succeeded" as StepStatus)),
      "+18055551000",
      [
        {
          id: "c-1",
          provider_call_id: "conv_1",
          direction: "inbound",
          from_e164: "+15559998888",
          started_at: null,
          duration_s: 84,
          status: "notified",
          summary: "Caller asked about Thursday morning.",
          caller_name: "Jamie Rivera",
          callback_number: "+18055557788",
          intent: "booking_request",
          urgency: 2,
        },
      ],
    );
    render(<StatusView tenantId="t-1" />);

    expect(await screen.findByText("Jamie Rivera")).toBeInTheDocument();
    expect(screen.getByText("Caller asked about Thursday morning.")).toBeInTheDocument();
    expect(screen.getByText(/as stated by the caller/)).toBeInTheDocument();
  });

  it("reports a load failure instead of rendering nothing", async () => {
    vi.spyOn(api, "getTenant").mockRejectedValue(
      new ApiError("tenant not found", { status: 404, code: "not_found", correlationId: "corr-x" }),
    );
    vi.spyOn(api, "getProvisioning").mockRejectedValue(new Error("unused"));
    vi.spyOn(api, "getPhoneOrNull").mockResolvedValue(null);
    vi.spyOn(api, "listCalls").mockResolvedValue([]);

    render(<StatusView tenantId="t-1" />);

    await screen.findByRole("alert");
    expect(screen.getByText("tenant not found")).toBeInTheDocument();
  });

  it("keeps polling while the run is still in flight", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stub(provisioning({ status: "config_generated" }));
    render(<StatusView tenantId="t-1" />);

    await waitFor(() => expect(api.getProvisioning).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(6500);

    expect(vi.mocked(api.getProvisioning).mock.calls.length).toBeGreaterThan(1);
  });

  it("stops fetching once the run has settled", async () => {
    // A page left open overnight must not keep hitting the API.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stub(
      provisioning({ status: "active" }, STEPS.map(() => "succeeded" as StepStatus)),
      "+18055551000",
    );
    render(<StatusView tenantId="t-1" />);

    await screen.findByText("+18055551000");
    // Let the effect settle: it re-runs once as `settled` flips true, which
    // costs one extra fetch and then tears the interval down.
    await vi.advanceTimersByTimeAsync(100);
    const afterSettle = vi.mocked(api.getProvisioning).mock.calls.length;

    await vi.advanceTimersByTimeAsync(30_000);

    expect(vi.mocked(api.getProvisioning).mock.calls.length).toBe(afterSettle);
    // Two at most: the mount fetch and the one the settle triggered.
    expect(afterSettle).toBeLessThanOrEqual(2);
  });
});
