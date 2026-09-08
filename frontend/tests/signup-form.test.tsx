/**
 * The signup form: submit, error display, and the redirect to the status page.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SignupForm } from "@/app/SignupForm";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

function fill() {
  fireEvent.change(screen.getByLabelText("Business name"), {
    target: { value: "Sunset Salon" },
  });
  fireEvent.change(screen.getByLabelText("Services offered"), {
    target: { value: "cuts, colour" },
  });
  fireEvent.change(screen.getByLabelText("Operating hours"), {
    target: { value: "Mon-Fri 9-6" },
  });
  fireEvent.change(screen.getByLabelText("Preferred area code"), {
    target: { value: "805" },
  });
  fireEvent.change(screen.getByLabelText("Notification email"), {
    target: { value: "owner@sunsetsalon.example.com" },
  });
  fireEvent.change(screen.getByLabelText("Contact phone"), {
    target: { value: "8055550142" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  push.mockReset();
});

describe("SignupForm", () => {
  it("renders every field the signup needs", () => {
    render(<SignupForm />);

    for (const label of [
      "Business name",
      "Business type",
      "Services offered",
      "Operating hours",
      "Greeting style",
      "Escalation rules",
      "Notification email",
      "Preferred area code",
      "Plan",
      "Contact phone",
    ]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });

  it("submits what the user typed and goes to the status page", async () => {
    const submit = vi.spyOn(api, "submitSignup").mockResolvedValue({
      tenant_id: "tenant-123",
      run_id: "run-1",
      tenant_status: "pending",
      provisioning_status: "draft",
      correlation_id: "corr",
      business_name: "Sunset Salon",
      contact_email: "owner@sunsetsalon.example.com",
      timezone: "America/Los_Angeles",
      created: true,
    });

    render(<SignupForm />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: /create my receptionist/i }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/status/tenant-123"));
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        business_name: "Sunset Salon",
        area_code: "805",
        contact_phone: "8055550142",
        business_type: "salon",
        plan: "starter",
      }),
    );
  });

  it("shows the API's message and reference when it rejects", async () => {
    vi.spyOn(api, "submitSignup").mockRejectedValue(
      new ApiError("area code must be exactly 3 digits", {
        status: 422,
        code: "invalid_input",
        correlationId: "corr-999",
        fieldErrors: { area_code: "must be 3 digits" },
      }),
    );

    render(<SignupForm />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: /create my receptionist/i }));

    await screen.findByRole("alert");
    expect(screen.getByText("area code must be exactly 3 digits")).toBeInTheDocument();
    expect(screen.getByText(/corr-999/)).toBeInTheDocument();
    // The per-field message appears next to the field it is about.
    expect(screen.getByText("must be 3 digits")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("re-enables the button after a failure so the user can try again", async () => {
    vi.spyOn(api, "submitSignup").mockRejectedValue(
      new ApiError("server unavailable", { status: 0, code: "network_error" }),
    );

    render(<SignupForm />);
    fill();
    const button = screen.getByRole("button", { name: /create my receptionist/i });
    fireEvent.click(button);

    await screen.findByRole("alert");
    expect(button).not.toBeDisabled();
  });
});
