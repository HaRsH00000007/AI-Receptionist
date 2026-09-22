/**
 * Onboarding: step validation, the submitted payload, error display, and the
 * redirect to the signed-in setup page, and the saved draft.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SignupWizard, restoreDraft, type WizardDraft } from "@/components/onboarding/SignupWizard";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { greetingPreset } from "@/lib/greetings";
import type { NumberSearchView, SignupResponse } from "@/lib/types";

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

const FRIENDLY_PRESET = greetingPreset("friendly", "Sunset Salon");

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

/** Fills step two, leaving the opening line on its default ("write it for me"). */
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
  // The form saves itself to the account as it changes; never a real request.
  vi.spyOn(api, "saveOnboardingDraft").mockResolvedValue({ data: null, updated_at: null });
});

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("SignupWizard", () => {
  it("walks through the steps and submits exactly what was entered", async () => {
    const submit = vi.spyOn(api, "submitOnboarding").mockResolvedValue(CREATED);

    render(<SignupWizard />);
    await fillToReview();
    clickActivate();

    // The business belongs to the signed-in account, so the owner watches
    // setup from the account rather than from an anonymous status link.
    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard/setup"));
    expect(submit).toHaveBeenCalledWith({
      business_name: "Sunset Salon",
      business_type: "salon",
      services: "cuts, colour",
      operating_hours: "Mon-Fri 9-6",
      greeting_style: "friendly",
      custom_greeting: "",
      selected_number: "",
      escalation_rules: "",
      notification_email: "owner@sunsetsalon.example.com",
      area_code: "805",
      plan: "starter",
      contact_phone: "8055550142",
    });
    // No password: the account already has one.
    expect(submit.mock.calls[0]![0]).not.toHaveProperty("password");
  });

  it("won't move past a step with missing details", () => {
    const submit = vi.spyOn(api, "submitOnboarding");

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
    const submit = vi.spyOn(api, "submitOnboarding").mockResolvedValue(CREATED);

    render(<SignupWizard />);
    await fillToReview();
    fireEvent.click(screen.getByRole("radio", { name: /^pro/i }));
    clickActivate();

    await vi.waitFor(() => expect(submit).toHaveBeenCalledWith(expect.objectContaining({ plan: "pro" })));
  });

  it("shows the forwarding choice on the review before activating", async () => {
    vi.spyOn(api, "submitOnboarding").mockResolvedValue(CREATED);

    render(<SignupWizard />);
    await fillToReview({ forward: true });
    expect(screen.getByText(/your existing number forwards to it/i)).toBeInTheDocument();
    clickActivate();

    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard/setup"));
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
    vi.spyOn(api, "submitOnboarding").mockRejectedValue(
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
    vi.spyOn(api, "submitOnboarding").mockRejectedValue(
      new ApiError("server unavailable", { status: 0, code: "network_error" }),
    );

    render(<SignupWizard />);
    await fillToReview();
    clickActivate();

    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: /activate receptionist/i })).not.toBeDisabled();
  });
});

describe("an account-first form", () => {
  it("asks for no password — the account already has one", async () => {
    render(<SignupWizard accountEmail="dana@example.com" />);
    await fillBusiness();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });

  it("starts the notification email as the account's email", async () => {
    render(<SignupWizard accountEmail="dana@example.com" />);
    await fillBusiness();
    expect(screen.getByLabelText("Notification email")).toHaveValue("dana@example.com");
  });

  it("saves progress to the account as it is filled in", async () => {
    const save = vi.mocked(api.saveOnboardingDraft);
    render(<SignupWizard accountEmail="dana@example.com" />);
    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Sunset Salon" } });

    await vi.waitFor(() => expect(save).toHaveBeenCalled(), { timeout: 3000 });
    const saved = save.mock.calls.at(-1)![0] as unknown as WizardDraft;
    expect(saved.values.business_name).toBe("Sunset Salon");
    expect(saved.step).toBe(0);
    expect(JSON.stringify(saved)).not.toMatch(/password/i);
    expect(await screen.findByText("Progress saved")).toBeInTheDocument();
  });

  it("reopens a saved form where it was left", async () => {
    const draft = restoreDraft(
      {
        step: 1,
        values: { business_name: "Sunset Salon", services: "cuts", contact_phone: 42 },
        greetingChoice: "custom",
        customGreeting: "Sunset Salon, how can I help?",
        unexpected: "ignored",
      },
      "dana@example.com",
    );
    render(<SignupWizard accountEmail="dana@example.com" draft={draft} />);

    expect(
      screen.getByRole("heading", { name: /how should your receptionist answer/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Your opening line")).toHaveValue("Sunset Salon, how can I help?");
    // A field of the wrong type in a draft falls back to its default.
    expect(draft.values.contact_phone).toBe("");
    expect(draft.values.notification_email).toBe("dana@example.com");
  });

  it("sends a contact-email clash back to the field", async () => {
    vi.spyOn(api, "submitOnboarding").mockRejectedValue(
      new ApiError("a business using this contact email is already set up", {
        status: 409,
        code: "contact_email_in_use",
        fieldErrors: { notification_email: "a business using this contact email is already set up" },
      }),
    );
    render(<SignupWizard />);
    await fillToReview();
    clickActivate();

    expect(
      await screen.findByRole("heading", { name: /how should your receptionist answer/i }),
    ).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("the opening line", () => {
  it("defaults to letting setup write one", async () => {
    const submit = vi.spyOn(api, "submitOnboarding").mockResolvedValue(CREATED);

    render(<SignupWizard />);
    await fillToReview();
    expect(screen.getByText(/written for you during setup/i)).toBeInTheDocument();
    clickActivate();

    await vi.waitFor(() =>
      expect(submit).toHaveBeenCalledWith(expect.objectContaining({ custom_greeting: "" })),
    );
  });

  it("sends the ready-made line when it is chosen", async () => {
    // What the customer read on screen is what the caller hears, so the exact
    // preset text is submitted rather than a code the backend re-expands.
    const submit = vi.spyOn(api, "submitOnboarding").mockResolvedValue(CREATED);

    render(<SignupWizard />);
    await fillBusiness();
    fireEvent.click(screen.getByRole("radio", { name: /^friendly/i }));
    fireEvent.click(screen.getByRole("radio", { name: /use this ready-made line/i }));
    fireEvent.change(screen.getByLabelText("Operating hours"), { target: { value: "Mon-Fri 9-6" } });
    fireEvent.change(screen.getByLabelText("Notification email"), {
      target: { value: "owner@sunsetsalon.example.com" },
    });
    clickContinue();
    await fillPhone();
    clickActivate();

    await vi.waitFor(() =>
      expect(submit).toHaveBeenCalledWith(expect.objectContaining({ custom_greeting: FRIENDLY_PRESET })),
    );
  });

  it("sends the owner's own wording", async () => {
    const submit = vi.spyOn(api, "submitOnboarding").mockResolvedValue(CREATED);
    const own = "Sunset Salon, Dana speaking — how can I help?";

    render(<SignupWizard />);
    await fillBusiness();
    fireEvent.click(screen.getByRole("radio", { name: /write my own/i }));
    fireEvent.change(screen.getByLabelText("Your opening line"), { target: { value: `  ${own} ` } });
    fireEvent.change(screen.getByLabelText("Operating hours"), { target: { value: "Mon-Fri 9-6" } });
    fireEvent.change(screen.getByLabelText("Notification email"), {
      target: { value: "owner@sunsetsalon.example.com" },
    });
    clickContinue();
    await fillPhone();

    expect(screen.getAllByText(`“${own}”`).length).toBeGreaterThan(0);
    clickActivate();

    await vi.waitFor(() =>
      expect(submit).toHaveBeenCalledWith(expect.objectContaining({ custom_greeting: own })),
    );
  });

  it("asks for the wording when the owner said they would write it", async () => {
    const submit = vi.spyOn(api, "submitOnboarding");

    render(<SignupWizard />);
    await fillBusiness();
    fireEvent.click(screen.getByRole("radio", { name: /write my own/i }));
    fireEvent.change(screen.getByLabelText("Operating hours"), { target: { value: "Mon-Fri 9-6" } });
    fireEvent.change(screen.getByLabelText("Notification email"), {
      target: { value: "owner@sunsetsalon.example.com" },
    });
    clickContinue();

    expect(screen.getByText(/write the line your receptionist should say/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /how should your receptionist answer/i })).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
  });

  it("previews the chosen line as what callers will hear", async () => {
    render(<SignupWizard />);
    await fillBusiness();
    fireEvent.click(screen.getByRole("radio", { name: /^friendly/i }));
    fireEvent.click(screen.getByRole("radio", { name: /use this ready-made line/i }));

    expect(screen.getAllByText(`“${FRIENDLY_PRESET}”`).length).toBeGreaterThan(0);
    expect(screen.getByText(/exactly what callers will hear/i)).toBeInTheDocument();
  });
});

describe("choosing a phone number", () => {
  const IN_AREA_CODE: NumberSearchView = {
    requested_area_code: "805",
    exact_match: true,
    strategy: "exact_area_code",
    numbers: [
      { e164: "+18055550100", area_code: "805", locality: "Santa Barbara", region: "CA" },
      { e164: "+18055550111", area_code: "805", locality: "Ventura", region: "CA" },
    ],
  };

  const NEARBY: NumberSearchView = {
    requested_area_code: "212",
    exact_match: false,
    strategy: "same_state",
    numbers: [
      { e164: "+16465550100", area_code: "646", locality: "New York", region: "NY" },
      { e164: "+17185550100", area_code: "718", locality: "Brooklyn", region: "NY" },
    ],
  };

  async function reachPhoneStep() {
    render(<SignupWizard />);
    await fillBusiness();
    fireEvent.click(screen.getByRole("radio", { name: /^friendly/i }));
    fireEvent.change(screen.getByLabelText("Operating hours"), { target: { value: "Mon-Fri 9-6" } });
    fireEvent.change(screen.getByLabelText("Notification email"), {
      target: { value: "owner@sunsetsalon.example.com" },
    });
    clickContinue();
    await screen.findByRole("heading", { name: /choose your phone number/i });
  }

  function search(areaCode: string) {
    fireEvent.change(screen.getByLabelText("Area code"), { target: { value: areaCode } });
    fireEvent.click(screen.getByRole("button", { name: /find numbers/i }));
  }

  it("offers the numbers available and submits the one picked", async () => {
    const find = vi.spyOn(api, "searchAvailableNumbers").mockResolvedValue(IN_AREA_CODE);
    const submit = vi.spyOn(api, "submitOnboarding").mockResolvedValue(CREATED);

    await reachPhoneStep();
    search("805");

    // Preselected, so Continue works without a second click.
    expect(await screen.findByRole("radio", { name: /555-0100/ })).toBeChecked();
    expect(find).toHaveBeenCalledWith("805");

    fireEvent.click(screen.getByRole("radio", { name: /555-0111/ }));
    clickContinue();
    await screen.findByRole("heading", { name: /review and activate/i });
    clickActivate();

    await vi.waitFor(() =>
      expect(submit).toHaveBeenCalledWith(
        expect.objectContaining({ selected_number: "+18055550111", area_code: "805" }),
      ),
    );
  });

  it("shows no alternatives when the requested area code has numbers", async () => {
    vi.spyOn(api, "searchAvailableNumbers").mockResolvedValue(IN_AREA_CODE);

    await reachPhoneStep();
    search("805");

    await screen.findByRole("radio", { name: /555-0100/ });
    expect(screen.queryByText(/no numbers are available/i)).not.toBeInTheDocument();
  });

  it("says so plainly when the area code is empty, and offers nearby numbers", async () => {
    vi.spyOn(api, "searchAvailableNumbers").mockResolvedValue(NEARBY);

    await reachPhoneStep();
    search("212");

    expect(await screen.findByText(/no numbers are available in 212/i)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /\(646\) 555-0100/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /\(718\) 555-0100/ })).toBeInTheDocument();
  });

  it("drops the offers when the area code changes", async () => {
    // They were numbers in the old area code; keeping them on screen would
    // invite someone to buy a number nowhere near where they asked.
    vi.spyOn(api, "searchAvailableNumbers").mockResolvedValue(IN_AREA_CODE);

    await reachPhoneStep();
    search("805");
    await screen.findByRole("radio", { name: /555-0100/ });

    fireEvent.change(screen.getByLabelText("Area code"), { target: { value: "213" } });

    expect(screen.queryByRole("radio", { name: /555-0100/ })).not.toBeInTheDocument();
  });

  it("lets setup choose the number when the search fails", async () => {
    // A vendor outage must not block signup: the run picks a number itself,
    // exactly as it did before numbers were shown.
    vi.spyOn(api, "searchAvailableNumbers").mockRejectedValue(
      new ApiError("Could not reach the server.", { status: 0, code: "network_error" }),
    );
    const submit = vi.spyOn(api, "submitOnboarding").mockResolvedValue(CREATED);

    await reachPhoneStep();
    search("805");

    expect(await screen.findByText(/couldn't load available numbers/i)).toBeInTheDocument();
    clickContinue();
    await screen.findByRole("heading", { name: /review and activate/i });
    clickActivate();

    await vi.waitFor(() =>
      expect(submit).toHaveBeenCalledWith(expect.objectContaining({ selected_number: "" })),
    );
  });

  it("reports an area code the backend rejects on the field itself", async () => {
    vi.spyOn(api, "searchAvailableNumbers").mockRejectedValue(
      new ApiError("unknown US area code", {
        status: 422,
        code: "invalid_input",
        fieldErrors: { area_code: "unknown US area code" },
      }),
    );

    await reachPhoneStep();
    search("999");

    expect(await screen.findByText("unknown US area code")).toBeInTheDocument();
  });
});
