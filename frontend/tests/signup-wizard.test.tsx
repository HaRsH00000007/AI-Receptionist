/**
 * Onboarding: step validation, the submitted payload, error display, and the
 * redirect to the provisioning status page.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SignupWizard } from "@/components/onboarding/SignupWizard";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { SignupResponse } from "@/lib/types";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const CREATED: SignupResponse = {
  tenant_id: "tenant-123",
  run_id: "run-1",
  tenant_status: "pending",
  provisioning_status: "draft",
  correlation_id: "corr",
  business_name: "Sunset Salon",
  contact_email: "owner@sunsetsalon.example.com",
  timezone: "America/Los_Angeles",
  created: true,
  status_token: "v1.grant.token",
};

function clickContinue() {
  fireEvent.click(screen.getByRole("button", { name: /continue/i }));
}

async function fillBusiness() {
  fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Sunset Salon" } });
  fireEvent.click(screen.getByRole("radio", { name: /salon & spa/i }));
  fireEvent.change(screen.getByLabelText("Services you offer"), { target: { value: "cuts, colour" } });
  fireEvent.change(screen.getByLabelText("Business phone"), { target: { value: "8055550142" } });
  clickContinue();
  await screen.findByRole("heading", { name: /how should your receptionist answer/i });
}

async function fillReceptionist() {
  fireEvent.click(screen.getByRole("radio", { name: /^friendly/i }));
  fireEvent.change(screen.getByLabelText("Operating hours"), { target: { value: "Mon-Fri 9-6" } });
  fireEvent.change(screen.getByLabelText("Notification email"), {
    target: { value: "owner@sunsetsalon.example.com" },
  });
  clickContinue();
  await screen.findByRole("heading", { name: /choose your phone number/i });
}

async function fillPhone({ forward = false } = {}) {
  if (forward) fireEvent.click(screen.getByRole("radio", { name: /keep my existing number/i }));
  fireEvent.change(screen.getByLabelText("Area code"), { target: { value: "805" } });
  clickContinue();
  await screen.findByRole("heading", { name: /review and activate/i });
}

async function fillToReview(options: { forward?: boolean } = {}) {
  await fillBusiness();
  await fillReceptionist();
  await fillPhone(options);
}

function clickActivate() {
  fireEvent.click(screen.getByRole("button", { name: /activate receptionist/i }));
}

beforeEach(() => {
  push.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("SignupWizard", () => {
  it("walks through the steps and submits exactly what was entered", async () => {
    const submit = vi.spyOn(api, "submitSignup").mockResolvedValue(CREATED);

    render(<SignupWizard />);
    await fillToReview();
    clickActivate();

    // The grant must travel with the redirect. Without it the status page has
    // no credential and every read is refused.
    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/status/tenant-123?t=v1.grant.token"));
    expect(submit).toHaveBeenCalledWith({
      business_name: "Sunset Salon",
      business_type: "salon",
      services: "cuts, colour",
      operating_hours: "Mon-Fri 9-6",
      greeting_style: "friendly",
      escalation_rules: "",
      notification_email: "owner@sunsetsalon.example.com",
      area_code: "805",
      plan: "starter",
      contact_phone: "8055550142",
    });
  });

  it("won't move past a step with missing details", () => {
    const submit = vi.spyOn(api, "submitSignup");

    render(<SignupWizard />);
    clickContinue();

    expect(screen.getByText("Enter your business name.")).toBeInTheDocument();
    expect(screen.getByText("Add at least one service.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /tell us about your business/i })).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
  });

  it("clears a field's error as soon as it is corrected", () => {
    render(<SignupWizard />);
    clickContinue();
    expect(screen.getByText("Enter your business name.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Sunset Salon" } });

    expect(screen.queryByText("Enter your business name.")).not.toBeInTheDocument();
  });

  it("sends the chosen plan", async () => {
    const submit = vi.spyOn(api, "submitSignup").mockResolvedValue(CREATED);

    render(<SignupWizard />);
    await fillToReview();
    fireEvent.click(screen.getByRole("radio", { name: /^pro/i }));
    clickActivate();

    await vi.waitFor(() => expect(submit).toHaveBeenCalledWith(expect.objectContaining({ plan: "pro" })));
  });

  it("carries the forwarding choice through to the status page", async () => {
    vi.spyOn(api, "submitSignup").mockResolvedValue(CREATED);

    render(<SignupWizard />);
    await fillToReview({ forward: true });
    expect(screen.getByText(/with your existing number forwarded to it/i)).toBeInTheDocument();
    clickActivate();

    await vi.waitFor(() =>
      expect(push).toHaveBeenCalledWith("/status/tenant-123?t=v1.grant.token&setup=forward"),
    );
  });

  it("shows exactly what will be set up before activating", async () => {
    render(<SignupWizard />);
    await fillToReview();

    expect(screen.getByText(/a local phone number in area code 805/i)).toBeInTheDocument();
    expect(screen.getByText(/call routing from that number/i)).toBeInTheDocument();
  });

  it("lets a section be edited from the review", async () => {
    render(<SignupWizard />);
    await fillToReview();

    fireEvent.click(screen.getByRole("button", { name: /edit business details/i }));

    expect(await screen.findByRole("heading", { name: /tell us about your business/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Business name")).toHaveValue("Sunset Salon");
  });

  it("shows the API's message and reference, and returns to the field it names", async () => {
    vi.spyOn(api, "submitSignup").mockRejectedValue(
      new ApiError("area code must be exactly 3 digits", {
        status: 422,
        code: "invalid_input",
        correlationId: "corr-999",
        fieldErrors: { area_code: "must be 3 digits" },
      }),
    );

    render(<SignupWizard />);
    await fillToReview();
    clickActivate();

    expect(await screen.findByRole("alert")).toHaveTextContent("area code must be exactly 3 digits");
    expect(screen.getByText(/corr-999/)).toBeInTheDocument();
    // The per-field message appears next to the field it is about, on its step.
    expect(screen.getByRole("heading", { name: /choose your phone number/i })).toBeInTheDocument();
    expect(screen.getByText("must be 3 digits")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("re-enables activation after a failure so the user can try again", async () => {
    vi.spyOn(api, "submitSignup").mockRejectedValue(
      new ApiError("server unavailable", { status: 0, code: "network_error" }),
    );

    render(<SignupWizard />);
    await fillToReview();
    clickActivate();

    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: /activate receptionist/i })).not.toBeDisabled();
  });
});
