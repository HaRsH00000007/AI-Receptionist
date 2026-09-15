/**
 * Activation: progress, the live number, failure, billing, and when polling stops.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StatusView } from "@/components/onboarding/StatusView";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { ProvisioningView, StepStatus, TenantView } from "@/lib/types";

const STEPS = [
  "validate",
  "generate_config",
  "billing_gate",
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
    billing: null,
    ...overrides,
  };
}

const ALL_DONE = STEPS.map(() => "succeeded" as StepStatus);

function stub(view: ProvisioningView, phone: string | null = null, calls: api.CallsResult = []) {
  vi.spyOn(api, "getTenant").mockResolvedValue(TENANT);
  vi.spyOn(api, "getProvisioning").mockResolvedValue(view);
  vi.spyOn(api, "getPhoneOrNull").mockResolvedValue(
    phone ? { e164: phone, status: "active", area_code: "805", purchased_at: null, released_at: null } : null,
  );
  vi.spyOn(api, "listCalls").mockResolvedValue(calls);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  cleanup();
});

describe("StatusView", () => {
  it("shows every step from the first render", async () => {
    stub(provisioning());
    render(<StatusView tenantId="t-1" statusToken="grant" />);

    expect(
      await screen.findByRole("heading", { name: /setting up your receptionist/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Provisioning phone number")).toBeInTheDocument();
    expect(screen.getByText("Verifying configuration")).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`0 of ${STEPS.length} steps`))).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Setup progress" })).toHaveAttribute("aria-valuenow", "0");
  });

  it("reads with the status grant rather than a bare tenant id", async () => {
    stub(provisioning());
    render(<StatusView tenantId="t-1" statusToken="grant" />);

    await waitFor(() => expect(api.getProvisioning).toHaveBeenCalledWith("t-1", "grant"));
  });

  it("shows the number and a test call once the run is active", async () => {
    stub(provisioning({ status: "active" }, ALL_DONE), "+18055551000");
    render(<StatusView tenantId="t-1" />);

    expect(await screen.findByText("+1 (805) 555-1000")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /your receptionist is live/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /call it now/i })).toHaveAttribute("href", "tel:+18055551000");
    expect(screen.getByText(new RegExp(`${STEPS.length} of ${STEPS.length} steps`))).toBeInTheDocument();
  });

  it("shows forwarding instructions when the business keeps its own number", async () => {
    stub(provisioning({ status: "active" }, ALL_DONE), "+18055551000");
    render(<StatusView tenantId="t-1" forwarding />);

    expect(
      await screen.findByRole("heading", { name: /keep your existing business number/i }),
    ).toBeInTheDocument();
  });

  it("explains a failure in words, keeping the reference and technical detail", async () => {
    stub(provisioning({ status: "compensated", last_error: "[vendor_error] twilio returned 400" }));
    render(<StatusView tenantId="t-1" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Setup did not complete");
    expect(alert).toHaveTextContent("A partner service returned an error");
    expect(alert).toHaveTextContent(/nothing is being charged/i);
    expect(screen.getByText("[vendor_error] twilio returned 400")).toBeInTheDocument();
    expect(screen.getAllByText(/corr-1/).length).toBeGreaterThan(0);
  });

  it("shows a step's own error next to that step", async () => {
    const view = provisioning();
    view.steps[STEPS.indexOf("purchase_number")] = {
      step_name: "purchase_number",
      status: "failed",
      attempt: 3,
      error: "[no_numbers_available] nothing in 805",
      started_at: null,
      finished_at: null,
    };
    stub(view);
    render(<StatusView tenantId="t-1" />);

    expect(await screen.findByText("No numbers were available in that area code")).toBeInTheDocument();
    expect(screen.getByText("[no_numbers_available] nothing in 805")).toBeInTheDocument();
    expect(screen.getByText("Attempt 3")).toBeInTheDocument();
  });

  it("renders a call summary but never the transcript", async () => {
    stub(provisioning({ status: "active" }, ALL_DONE), "+18055551000", [
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
    ]);
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

    expect(await screen.findByRole("alert")).toHaveTextContent("tenant not found");
    expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute("href", "/login");
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
    stub(provisioning({ status: "active" }, ALL_DONE), "+18055551000");
    render(<StatusView tenantId="t-1" />);

    await screen.findByText("+1 (805) 555-1000");
    await vi.advanceTimersByTimeAsync(100);
    const afterSettle = vi.mocked(api.getProvisioning).mock.calls.length;

    await vi.advanceTimersByTimeAsync(30_000);

    expect(vi.mocked(api.getProvisioning).mock.calls.length).toBe(afterSettle);
    expect(afterSettle).toBeLessThanOrEqual(2);
  });
});

describe("StatusView — billing", () => {
  it("explains a run parked on billing instead of showing an error", async () => {
    // Not a failure: something the customer can fix. An error would be both
    // wrong and a support ticket.
    stub(
      provisioning({
        status: "billing_blocked",
        billing: { entitled: false, plan: "trial", status: "trialing", trial_ends_at: null, reason: "trial_expired" },
      }),
    );

    render(<StatusView tenantId="t-1" />);

    expect(await screen.findByRole("heading", { name: /waiting for your subscription/i })).toBeInTheDocument();
    expect(screen.getByText(/free trial has ended/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("falls back to honest wording for an unrecognised reason", async () => {
    stub(
      provisioning({
        status: "billing_blocked",
        billing: { entitled: false, plan: null, status: null, trial_ends_at: null, reason: "some_future_reason" },
      }),
    );

    render(<StatusView tenantId="t-1" />);

    expect(await screen.findByText(/need an active subscription/i)).toBeInTheDocument();
  });
});
