/**
 * Where a signed-in customer belongs.
 *
 * The rule that must never break: a business whose receptionist is live is
 * never shown onboarding or setup again. Everything else follows from the
 * tenant's state, and every combination is listed here rather than sampled.
 */

import { describe, expect, it } from "vitest";

import { onboardingRedirect, redirectFor, signupRedirect, stageOf } from "@/lib/routing";
import type { ProvisioningStatus } from "@/lib/types";

const MEMBER = { memberships: [{ tenant_id: "t1" }] };

describe("the stage a tenant is in", () => {
  it.each<[string, string, ProvisioningStatus, "dashboard" | "setup" | "onboarding"]>([
    ["a live receptionist", "active", "active", "dashboard"],
    ["setup running", "pending", "number_purchased", "setup"],
    ["setup queued", "pending", "draft", "setup"],
    ["waiting on billing", "pending", "billing_blocked", "setup"],
    ["setup failed", "failed", "failed", "setup"],
    ["rolled back", "failed", "compensated", "setup"],
    // A business that is over sends its owner to set up a new one, not to a
    // status page that can only report that it is over.
    ["cancelled", "cancelled", "active", "onboarding"],
    ["abandoned", "abandoned", "compensated", "onboarding"],
  ])("%s -> %s", (_label, tenantStatus, provisioning, expected) => {
    expect(stageOf(tenantStatus, provisioning)).toBe(expected);
  });
});

describe("keeping each tenant on the right page", () => {
  it("never shows a live customer the setup page", () => {
    expect(redirectFor("dashboard", "/dashboard/setup")).toBe("/dashboard");
  });

  it("leaves a live customer's deep links alone", () => {
    for (const path of ["/dashboard", "/dashboard/calls", "/dashboard/settings/billing"]) {
      expect(redirectFor("dashboard", path)).toBeNull();
    }
  });

  it("sends anyone not live to setup, from anywhere in the dashboard", () => {
    for (const path of ["/dashboard", "/dashboard/calls", "/dashboard/integrations"]) {
      expect(redirectFor("setup", path)).toBe("/dashboard/setup");
    }
    expect(redirectFor("setup", "/dashboard/setup")).toBeNull();
  });
});

describe("the onboarding page", () => {
  it("sends a visitor who is not signed in to create an account first", () => {
    expect(onboardingRedirect(null, false)).toBe("/signup");
    expect(onboardingRedirect(null, true)).toBe("/signup");
  });

  it("shows the form to a signed-in user with no business yet", () => {
    expect(onboardingRedirect({ memberships: [] }, false)).toBeNull();
  });

  it("sends an existing customer to their dashboard instead of the form", () => {
    expect(onboardingRedirect(MEMBER, false)).toBe("/dashboard");
  });

  it("lets an existing customer deliberately set up another business", () => {
    expect(onboardingRedirect(MEMBER, true)).toBeNull();
  });
});

describe("the create-account page", () => {
  it("is shown to someone without an account", () => {
    expect(signupRedirect(null)).toBeNull();
  });

  it("sends someone already signed in on to the business form", () => {
    expect(signupRedirect(MEMBER)).toBe("/get-started");
  });
});

describe("someone who deleted their receptionist", () => {
  it("is sent to set a new one up, from wherever they land", () => {
    // Signing in lands on /dashboard; a bookmark may land anywhere. Both go to
    // the form, because there is nothing else for this account to look at.
    expect(redirectFor("onboarding", "/dashboard")).toBe("/get-started?another=1");
    expect(redirectFor("onboarding", "/dashboard/setup")).toBe("/get-started?another=1");
    expect(redirectFor("onboarding", "/dashboard/calls")).toBe("/get-started?another=1");
  });

  it("is not bounced back out of the form by the gate", () => {
    // The membership still exists — to the cancelled business — so without
    // `another` the form would send them to the dashboard and the dashboard
    // would send them back. `another=1` is what breaks that loop.
    expect(onboardingRedirect(MEMBER, true)).toBeNull();
    expect(onboardingRedirect(MEMBER, false)).toBe("/dashboard");
  });
});
