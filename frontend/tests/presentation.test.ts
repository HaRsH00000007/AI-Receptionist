/**
 * The pure presentation helpers: how backend values become words.
 */

import { describe, expect, it } from "vitest";

import { buildConversationPreview } from "@/components/app/BusinessDetails";
import { formatDuration, formatPhone, humanize, initials } from "@/lib/format";
import {
  callMatchesStatusFilter,
  friendlyError,
  isUrgent,
  receptionistState,
  stepLabel,
} from "@/lib/status";
import type { BusinessProfileView } from "@/lib/types";

describe("formatting", () => {
  it("formats North American numbers and leaves others alone", () => {
    expect(formatPhone("+18055550100")).toBe("+1 (805) 555-0100");
    expect(formatPhone("+442071838750")).toBe("+442071838750");
    expect(formatPhone(null)).toBe("—");
  });

  it("formats durations", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(95)).toBe("1m 35s");
    expect(formatDuration(120)).toBe("2m");
    expect(formatDuration(3_700)).toBe("1h 1m");
    expect(formatDuration(null)).toBe("—");
  });

  it("turns codes into words", () => {
    expect(humanize("booking_request")).toBe("Booking request");
    expect(initials("Dana Reyes")).toBe("DR");
    expect(initials("")).toBe("?");
  });
});

describe("status", () => {
  it("never shows a raw error code as the headline", () => {
    const known = friendlyError("[no_numbers_available] nothing in 805");
    expect(known.code).toBe("no_numbers_available");
    expect(known.title).toMatch(/no numbers were available/i);
    expect(known.technical).toBe("[no_numbers_available] nothing in 805");

    const unknown = friendlyError("[something_new] exploded");
    expect(unknown.title).toBe("Setup didn't finish");
    expect(unknown.title).not.toContain("something_new");
  });

  it("describes the receptionist from tenant and run state", () => {
    expect(receptionistState("active", "active")).toBe("live");
    expect(receptionistState("pending", "number_purchased")).toBe("setting_up");
    expect(receptionistState("pending", "billing_blocked")).toBe("billing");
    expect(receptionistState("failed", "compensated")).toBe("attention");
    expect(receptionistState("cancelled", "active")).toBe("inactive");
  });

  it("labels steps for a person", () => {
    expect(stepLabel("purchase_number")).toBe("Provisioning phone number");
    expect(stepLabel("a_future_step")).toBe("A future step");
  });

  it("groups in-progress call statuses together", () => {
    expect(callMatchesStatusFilter("received", "processing")).toBe(true);
    expect(callMatchesStatusFilter("transcribed", "processing")).toBe(true);
    expect(callMatchesStatusFilter("notified", "processing")).toBe(false);
    expect(callMatchesStatusFilter("notified", "")).toBe(true);
  });

  it("flags urgency of 4 and above", () => {
    expect(isUrgent(4)).toBe(true);
    expect(isUrgent(3)).toBe(false);
    expect(isUrgent(null)).toBe(false);
  });
});

describe("the configuration preview", () => {
  const PROFILE: BusinessProfileView = {
    services: ["Cuts", "Colour"],
    hours_raw: "Mon-Sat 9-6",
    hours: {
      timezone: "America/Los_Angeles",
      days: [{ day: "saturday", closed: true, opens_at: null, closes_at: null }],
    },
    greeting_style: "friendly",
    escalation_raw: "Tell me about emergencies",
    escalation: {
      default_mode: "take_message",
      rules: [{ when: "emergency", mode: "notify_owner" }],
      notify_email: "owner@example.com",
      notify_phone: null,
    },
    greeting: "Hello from the live config!",
    config_version: 2,
  };

  it("uses the live greeting verbatim and answers from the business's own details", () => {
    const lines = buildConversationPreview("Sunset Salon", PROFILE);
    const text = lines.map((line) => line.text).join(" ");

    expect(lines[0]).toEqual({ from: "receptionist", text: "Hello from the live config!" });
    expect(text).toMatch(/closed on saturdays/i);
    expect(text).toMatch(/cuts and colour/i);
    expect(text).toMatch(/let the team know right away/i);
  });

  it("falls back to a style sample when nothing is live yet", () => {
    const lines = buildConversationPreview("Sunset Salon", null);
    expect(lines[0]?.text).toContain("Sunset Salon");
  });
});
