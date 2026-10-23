/**
 * The signed-in flows end to end in the browser: where each kind of customer
 * lands, and what they can do there.
 *
 *   existing, live     -> dashboard, and kept off setup
 *   still provisioning -> setup page with live progress
 *   setup failed       -> setup page with a retry (for an owner)
 *   signed out, "Get started"     -> create an account first
 *   new account, no business      -> the business form, resumed from its draft
 *   signed in, visiting "Get started" -> sent to the dashboard, not the form
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardGate } from "@/components/app/DashboardGate";
import { TenantWorkspace } from "@/components/app/TenantWorkspace";
import { SetupView } from "@/components/app/views/SetupView";
import { WorkspaceFrame } from "@/components/app/WorkspaceFrame";
import { SignupForm } from "@/components/auth/SignupForm";
import { GetStartedGate } from "@/components/onboarding/GetStartedGate";
import { ToastProvider } from "@/components/ui/Toast";
import type { ProvisioningStatus, SessionView } from "@/lib/types";

const navigation = vi.hoisted(() => ({
  pathname: "/dashboard",
  search: "",
  replace: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: navigation.push, replace: navigation.replace, refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(navigation.search),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getTenant: vi.fn(),
    getProvisioning: vi.fn(),
    getPhoneOrNull: vi.fn(),
    getAgentOrNull: vi.fn(),
    getUsage: vi.fn(),
    listCalls: vi.fn(),
    getProfileOrNull: vi.fn(),
    retryProvisioning: vi.fn(),
    getSessionOrNull: vi.fn(),
    getOnboardingDraft: vi.fn(),
    saveOnboardingDraft: vi.fn(),
    getWebsiteSetupOrNull: vi.fn(),
    startWebsiteSetup: vi.fn(),
    discardWebsiteSetup: vi.fn(),
    saveWebsiteKnowledge: vi.fn(),
    saveWebsiteChoices: vi.fn(),
    confirmWebsiteSetup: vi.fn(),
    registerAccount: vi.fn(),
    logout: vi.fn(),
  };
});

const api = await import("@/lib/api");

const OWNER: SessionView = {
  user_id: "u1",
  email: "owner@sunset.example",
  full_name: "Dana Reyes",
  is_platform_admin: false,
  active_tenant_id: "t1",
  memberships: [{ tenant_id: "t1", name: "Sunset Salon", role: "owner", status: "active" }],
  impersonated: false,
};

function givenTenant(tenantStatus: string, provisioning: ProvisioningStatus) {
  vi.mocked(api.getTenant).mockResolvedValue({
    id: "t1",
    name: "Sunset Salon",
    business_type: "salon",
    status: tenantStatus,
    plan: "starter",
    timezone: "America/Los_Angeles",
    contact_email: "owner@sunset.example",
    area_code: "805",
    created_at: "2026-09-01T00:00:00Z",
  });
  vi.mocked(api.getProvisioning).mockResolvedValue({
    run_id: "r1",
    tenant_id: "t1",
    status: provisioning,
    current_step: null,
    attempt: 1,
    last_error: provisioning === "failed" ? "[vendor_error] twilio returned 500" : null,
    correlation_id: "corr-1",
    next_attempt_at: null,
    started_at: null,
    finished_at: null,
    steps: [],
    completed_steps: provisioning === "active" ? 8 : 3,
    total_steps: 8,
    billing: null,
  });
  vi.mocked(api.getPhoneOrNull).mockResolvedValue(null);
  vi.mocked(api.getAgentOrNull).mockResolvedValue(null);
  vi.mocked(api.getUsage).mockResolvedValue({
    period_start: "2026-09-01",
    period_end: "2026-10-01",
    call_count: 0,
    call_minutes: 0,
    included_minutes: 500,
    percent_used: 0,
    over_limit: false,
    warning: false,
    blocks_on_overage: false,
    plan: "starter",
  });
  vi.mocked(api.listCalls).mockResolvedValue([]);
  vi.mocked(api.getProfileOrNull).mockResolvedValue(null);
}

/** Status per tenant id, so a two-business account can differ per business. */
function givenTenants(statuses: Record<string, string>) {
  vi.mocked(api.getTenant).mockImplementation(async (id: string) => ({
    id,
    name: id === "t2" ? "Sunset Spa" : "Sunset Salon",
    business_type: "salon",
    status: statuses[id] ?? "active",
    plan: "starter",
    timezone: "America/Los_Angeles",
    contact_email: "owner@sunset.example",
    area_code: "805",
    created_at: "2026-09-01T00:00:00Z",
  }));
}

function renderWorkspace(children = <p>page content</p>, session: SessionView = OWNER) {
  return render(
    <ToastProvider>
      <TenantWorkspace session={session} onSignedOut={() => {}}>
        <WorkspaceFrame>{children}</WorkspaceFrame>
      </TenantWorkspace>
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  navigation.pathname = "/dashboard";
  navigation.search = "";
});

afterEach(cleanup);

describe("an existing customer", () => {
  it("whose receptionist is live gets the dashboard", async () => {
    givenTenant("active", "active");
    renderWorkspace();
    expect(await screen.findByText("page content")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Dashboard" })).toBeInTheDocument();
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it("who is live is never left on the setup page", async () => {
    givenTenant("active", "active");
    navigation.pathname = "/dashboard/setup";
    renderWorkspace();
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByText("page content")).not.toBeInTheDocument();
  });
});

describe("a customer whose setup is still running", () => {
  it("is sent to the setup page from anywhere in the dashboard", async () => {
    givenTenant("pending", "number_purchased");
    navigation.pathname = "/dashboard/calls";
    renderWorkspace();
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/dashboard/setup"));
  });

  it("sees live progress there, without the dashboard's navigation", async () => {
    givenTenant("pending", "number_purchased");
    navigation.pathname = "/dashboard/setup";
    renderWorkspace(<SetupView />);
    expect(
      await screen.findByRole("heading", { name: /setting up your receptionist/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/3 of 8 steps complete/i)).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Dashboard" })).not.toBeInTheDocument();
  });
});

describe("a customer whose setup failed", () => {
  it("is shown what went wrong and can retry", async () => {
    givenTenant("failed", "failed");
    navigation.pathname = "/dashboard/setup";
    vi.mocked(api.retryProvisioning).mockResolvedValue({
      run_id: "r1",
      status: "draft",
      steps_reset: ["purchase_number"],
    });
    renderWorkspace(<SetupView />);

    expect(await screen.findByRole("heading", { name: /setup needs attention/i })).toBeInTheDocument();
    expect(screen.getByText(/a partner service returned an error/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(api.retryProvisioning).toHaveBeenCalledWith("t1"));
  });

  it("offers no retry to a plain member", async () => {
    givenTenant("failed", "failed");
    navigation.pathname = "/dashboard/setup";
    renderWorkspace(<SetupView />, {
      ...OWNER,
      memberships: [{ tenant_id: "t1", name: "Sunset Salon", role: "member", status: "active" }],
    });
    await screen.findByRole("heading", { name: /setup needs attention/i });
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
    expect(screen.getByText(/ask the owner of this account/i)).toBeInTheDocument();
  });
});

describe("someone who deleted their receptionist", () => {
  it("is taken to set up a new one when they sign in again", async () => {
    // The bug this covers: signing in landed on "This receptionist isn't
    // active" — a page whose only action is the one we now go to directly.
    givenTenant("cancelled", "active");

    renderWorkspace();

    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith("/get-started?another=1"),
    );
    expect(screen.queryByText("page content")).not.toBeInTheDocument();
    expect(screen.queryByText(/isn't active/i)).not.toBeInTheDocument();
  });

  it("does not ping-pong when every business they own is cancelled", async () => {
    // The regression: each cancelled business said "you own another, open that"
    // about the other, so the org switcher flipped between them forever and
    // every hop refetched seven endpoints. One pass, then the form.
    givenTenant("cancelled", "active");
    givenTenants({ t1: "cancelled", t2: "cancelled" });
    const bothDead: SessionView = {
      ...OWNER,
      memberships: [
        { tenant_id: "t1", name: "Roman Wellness Spa", role: "owner", status: "active" },
        { tenant_id: "t2", name: "Scalezix", role: "owner", status: "active" },
      ],
    };

    renderWorkspace(<p>page content</p>, bothDead);

    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith("/get-started?another=1"),
    );
    // Each business is opened at most once: two tenants, two loads, no loop.
    // The property that matters: the search moves forward and stops. Once it
    // has moved on to the second business it never goes back to the first,
    // which is what the ping-pong did — forever, seven requests a lap.
    const opened = vi.mocked(api.getTenant).mock.calls.map(([id]) => id);
    expect(opened).toContain("t2");
    expect(opened.lastIndexOf("t1")).toBeLessThan(opened.indexOf("t2"));
    // Bounded: each business is visited once (the workspace loads each one
    // twice by itself), not endlessly. A ping-pong has no upper bound at all.
    expect(opened.length).toBeLessThanOrEqual(4);
  });

  it("opens their other business instead, when they still have one", async () => {
    // Two businesses, the open one cancelled: switching beats offering to
    // create a third.
    givenTenant("cancelled", "active");
    givenTenants({ t1: "cancelled", t2: "active" });
    const twoBusinesses: SessionView = {
      ...OWNER,
      memberships: [
        { tenant_id: "t1", name: "Sunset Salon", role: "owner", status: "active" },
        { tenant_id: "t2", name: "Sunset Spa", role: "owner", status: "active" },
      ],
    };

    renderWorkspace(<p>page content</p>, twoBusinesses);

    await waitFor(() =>
      expect(vi.mocked(api.getTenant).mock.calls.some(([id]) => id === "t2")).toBe(true),
    );
    // It opens the live business and stays there: no offer to create a third.
    expect(navigation.replace).not.toHaveBeenCalledWith("/get-started?another=1");
  });
});

describe("the Get started page", () => {
  it("sends a signed-in customer to their dashboard instead of the signup form", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue(OWNER);
    render(<GetStartedGate />);
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByLabelText("Business name")).not.toBeInTheDocument();
  });

  it("sends a new visitor to create an account first", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue(null);
    render(<GetStartedGate />);
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/signup"));
    expect(screen.queryByLabelText("Business name")).not.toBeInTheDocument();
  });

  it("shows a new account the business form, resumed from its saved draft", async () => {
    navigation.search = "mode=manual";
    vi.mocked(api.getSessionOrNull).mockResolvedValue({ ...OWNER, memberships: [] });
    vi.mocked(api.getOnboardingDraft).mockResolvedValue({
      data: { step: 0, values: { business_name: "Sunset Salon" } },
      updated_at: "2026-09-22T10:00:00Z",
    });
    vi.mocked(api.saveOnboardingDraft).mockResolvedValue({ data: null, updated_at: null });
    render(<GetStartedGate />);

    expect(await screen.findByLabelText("Business name")).toHaveValue("Sunset Salon");
    expect(screen.getByText(/signed in as/i)).toHaveTextContent("owner@sunset.example");
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it("still shows the form if the saved draft can't be read", async () => {
    navigation.search = "mode=manual";
    vi.mocked(api.getSessionOrNull).mockResolvedValue({ ...OWNER, memberships: [] });
    vi.mocked(api.getOnboardingDraft).mockRejectedValue(new api.ApiError("down", { status: 0 }));
    vi.mocked(api.saveOnboardingDraft).mockResolvedValue({ data: null, updated_at: null });
    render(<GetStartedGate />);
    expect(await screen.findByLabelText("Business name")).toHaveValue("");
  });

  it("lets a customer deliberately start another business", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue(OWNER);
    vi.mocked(api.getOnboardingDraft).mockResolvedValue({ data: null, updated_at: null });
    navigation.search = "another=1&mode=manual";
    render(<GetStartedGate />);
    expect(await screen.findByLabelText("Business name")).toBeInTheDocument();
  });

  it("offers both ways to set up before either one starts", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue({ ...OWNER, memberships: [] });
    vi.mocked(api.getOnboardingDraft).mockResolvedValue({ data: null, updated_at: null });

    render(<GetStartedGate />);

    expect(
      await screen.findByRole("heading", { name: /how would you like to create/i }),
    ).toBeInTheDocument();
    // Neither flow has started: no form, and nothing has been sent anywhere.
    expect(screen.queryByLabelText("Business name")).not.toBeInTheDocument();
    expect(api.startWebsiteSetup).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /use my website/i }));

    // The choice is recorded in the URL, so a refresh stays on it.
    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith("/get-started?mode=website"),
    );
  });

  it("does not reopen a website setup that already built a business", async () => {
    // The bug: after deleting the receptionist, signing in reopened step 4 of
    // the wizard — the review screen of a business that no longer exists.
    vi.mocked(api.getSessionOrNull).mockResolvedValue({ ...OWNER, memberships: [] });
    vi.mocked(api.getOnboardingDraft).mockResolvedValue({ data: null, updated_at: null });
    vi.mocked(api.getWebsiteSetupOrNull).mockResolvedValue({
      id: "job-1",
      status: "confirmed",
      website_url: "https://sunset.example.com/",
      attempt: 1,
      last_error: null,
      ready: true,
      tenant_id: "t9",
      crawl: null,
      knowledge: {
        business_name: "Sunset Salon",
        business_summary: "A salon.",
        operating_hours: "Tue-Sat 9-6",
        services: ["Cuts"],
        items: [],
        questions: [],
        sms_templates: [],
      },
      choices: null,
      created_at: "2026-10-13T00:00:00Z",
      updated_at: "2026-10-13T00:00:00Z",
    });

    render(<GetStartedGate />);

    // Back to the start, with both options offered.
    expect(
      await screen.findByRole("heading", { name: /how would you like to create/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /what your agent learned/i })).not.toBeInTheDocument();
  });

  it("resumes a half-finished website setup without asking again", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue({ ...OWNER, memberships: [] });
    vi.mocked(api.getOnboardingDraft).mockResolvedValue({ data: null, updated_at: null });
    vi.mocked(api.getWebsiteSetupOrNull).mockResolvedValue({
      id: "job-1",
      status: "ready",
      website_url: "https://sunset.example.com/",
      attempt: 1,
      last_error: null,
      ready: true,
      tenant_id: null,
      crawl: {
        site: "https://sunset.example.com/",
        pages_read: 3,
        page_urls: ["https://sunset.example.com/"],
        skipped: 0,
        failed: 0,
        duplicates: 0,
        robots_blocked: 0,
        truncated: false,
        reasons: {},
      },
      knowledge: {
        business_name: "Sunset Salon",
        business_summary: "A salon.",
        operating_hours: "Tue-Sat 9-6",
        services: ["Cuts"],
        items: [
          {
            id: "item-0",
            category: "pricing",
            title: "Prices",
            content: "A cut is thirty dollars.",
            source_url: "https://sunset.example.com/",
            needs_review: false,
            enabled: true,
          },
        ],
        questions: [],
        sms_templates: [],
      },
      choices: null,
      created_at: "2026-10-13T00:00:00Z",
      updated_at: "2026-10-13T00:00:00Z",
    });

    render(<GetStartedGate />);

    // Straight back to the review screen, not the choice screen.
    expect(
      await screen.findByRole("heading", { name: /what your agent learned/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Prices")).toBeInTheDocument();
    expect(screen.getByText(/read 3 pages/i)).toBeInTheDocument();
  });
});

describe("the dashboard for an account with no business yet", () => {
  it("resumes the setup form instead of showing an empty dashboard", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue({ ...OWNER, memberships: [] });
    render(
      <DashboardGate>
        <p>page content</p>
      </DashboardGate>,
    );
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/get-started"));
    expect(screen.queryByText("page content")).not.toBeInTheDocument();
  });

  it("does not bounce an account whose businesses are all cancelled", async () => {
    // The loop this guards against: the gate sends them to the form because no
    // business is workable, and the form sends anyone holding a membership back
    // to the dashboard. Both pages then flicker and neither ever renders.
    // `another=1` is the door that breaks it.
    vi.mocked(api.getSessionOrNull).mockResolvedValue({
      ...OWNER,
      active_tenant_id: null,
      memberships: [
        { tenant_id: "t1", name: "Careful Legal", role: "owner", status: "cancelled" },
        { tenant_id: "t2", name: "Second Try", role: "owner", status: "cancelled" },
      ],
    });
    render(
      <DashboardGate>
        <p>page content</p>
      </DashboardGate>,
    );

    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith("/get-started?another=1"),
    );
    expect(navigation.replace).not.toHaveBeenCalledWith("/get-started");
    // And nothing tenant-shaped is mounted behind the redirect, which is what
    // left the page sitting on "Loading your receptionist…".
    expect(screen.queryByText("page content")).not.toBeInTheDocument();
    expect(api.getTenant).not.toHaveBeenCalled();
  });
});

describe("creating an account", () => {
  it("creates the account, then continues to the business form", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue(null);
    vi.mocked(api.registerAccount).mockResolvedValue({ ...OWNER, memberships: [] });
    render(<SignupForm />);

    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Dana Reyes" } });
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: " dana@sunset.example " },
    });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "long enough pw" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() =>
      expect(api.registerAccount).toHaveBeenCalledWith(
        "Dana Reyes",
        "dana@sunset.example",
        "long enough pw",
      ),
    );
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/get-started"));
  });

  it("checks the fields before asking the server", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue(null);
    render(<SignupForm />);
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByText("Enter your name.")).toBeInTheDocument();
    expect(screen.getByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument();
    expect(api.registerAccount).not.toHaveBeenCalled();
  });

  it("points an existing address to sign-in", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue(null);
    vi.mocked(api.registerAccount).mockRejectedValue(
      new api.ApiError("an account with this email already exists", {
        status: 409,
        code: "account_exists",
      }),
    );
    render(<SignupForm />);
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Dana" } });
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "dana@sunset.example" },
    });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "long enough pw" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByText(/an account with this email already exists/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /sign in instead/i })).toHaveAttribute("href", "/login");
    // The password is cleared rather than left in the field after a refusal.
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("sends someone already signed in on to the business form", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue(OWNER);
    render(<SignupForm />);
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/get-started"));
  });
});

describe("getting back out of the website flow", () => {
  function givenNoDraft() {
    vi.mocked(api.getSessionOrNull).mockResolvedValue({ ...OWNER, memberships: [] });
    vi.mocked(api.getOnboardingDraft).mockResolvedValue({ data: null, updated_at: null });
    vi.mocked(api.getWebsiteSetupOrNull).mockResolvedValue(null);
  }

  /** A website setup that has been read and is waiting on the review. */
  function givenReadyDraft() {
    givenNoDraft();
    vi.mocked(api.getWebsiteSetupOrNull).mockResolvedValue({
      id: "job-1",
      status: "ready",
      website_url: "https://sunset.example.com/",
      attempt: 1,
      last_error: null,
      ready: true,
      tenant_id: null,
      crawl: {
        site: "sunset.example.com",
        pages_read: 3,
        page_urls: ["https://sunset.example.com/prices"],
        skipped: 0,
        failed: 0,
        duplicates: 0,
        robots_blocked: 0,
        truncated: false,
        reasons: {},
      },
      knowledge: {
        business_name: "Sunset Salon",
        business_summary: "A salon on the coast.",
        operating_hours: "Tue-Sat 9-6",
        services: ["Haircuts"],
        items: [
          {
            id: "k1",
            category: "service",
            title: "Haircuts",
            content: "A standard haircut costs thirty dollars.",
            source_url: "https://sunset.example.com/prices",
            needs_review: false,
            enabled: true,
          },
        ],
        questions: [
          { key: "name", label: "Name", prompt: "Ask for their name.", builtin: true, enabled: true },
        ],
        sms_templates: [],
      },
      choices: null,
      created_at: "2026-10-13T00:00:00Z",
      updated_at: "2026-10-13T00:00:00Z",
    });
  }

  it("goes back from the first step to the choice of both ways", async () => {
    // Without this, picking "use my website" was a one-way door: the first
    // step had no Back at all.
    navigation.search = "mode=website";
    givenNoDraft();

    render(<GetStartedGate />);
    fireEvent.click(await screen.findByRole("button", { name: /back/i }));

    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/get-started?mode=choose"));
  });

  it("asks for the choice screen by name, so an unfinished draft cannot undo it", async () => {
    // The bug, as a customer met it: a read website opens on the review, and
    // Back walks them review -> website -> basics. From basics there is nothing
    // left to go back to but the choice of both ways — and that press used to
    // do nothing at all, because stripping the mode let the draft put the
    // wizard straight back on screen.
    navigation.search = "mode=website";
    givenReadyDraft();

    render(<GetStartedGate />);
    // Review -> website field.
    fireEvent.click(await screen.findByRole("button", { name: /back/i }));
    expect(
      await screen.findByRole("heading", { name: /teach your agent about your business/i }),
    ).toBeInTheDocument();
    // Website field -> basics.
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(
      await screen.findByRole("heading", { name: /first, a few basics/i }),
    ).toBeInTheDocument();
    // Basics -> out of the flow.
    fireEvent.click(screen.getByRole("button", { name: /back/i }));

    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/get-started?mode=choose"));
  });

  it("takes the half-finished setup with it when someone backs out", async () => {
    // Nothing the customer typed was ever saved, so the crawl is all there is
    // to clean up — and leaving it behind is what used to resurrect the flow.
    navigation.search = "mode=website";
    givenReadyDraft();
    vi.mocked(api.discardWebsiteSetup).mockResolvedValue(undefined);

    render(<GetStartedGate />);
    fireEvent.click(await screen.findByRole("button", { name: /back/i }));
    expect(
      await screen.findByRole("heading", { name: /teach your agent about your business/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(await screen.findByRole("heading", { name: /first, a few basics/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /back/i }));

    await waitFor(() => expect(api.discardWebsiteSetup).toHaveBeenCalled());
  });

  it("saves nothing while the customer walks through the review", async () => {
    // The whole point: until the create button, every screen lives in the
    // browser. A step that wrote as it went would leave a stranger's draft on
    // the account for anyone who changed their mind.
    navigation.search = "mode=website";
    givenReadyDraft();

    render(<GetStartedGate />);
    expect(
      await screen.findByRole("heading", { name: /here's what your agent learned/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(
      await screen.findByRole("heading", { name: /what info do you want to ask callers for/i }),
    ).toBeInTheDocument();

    expect(api.saveWebsiteKnowledge).not.toHaveBeenCalled();
    expect(api.saveWebsiteChoices).not.toHaveBeenCalled();
    expect(api.confirmWebsiteSetup).not.toHaveBeenCalled();
  });

  it("shows both options on the choice screen even with a draft waiting", async () => {
    navigation.search = "mode=choose";
    givenReadyDraft();

    render(<GetStartedGate />);

    expect(
      await screen.findByRole("heading", { name: /how would you like to create/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Use my website")).toBeInTheDocument();
    expect(screen.getByText("Set up manually")).toBeInTheDocument();
  });

  it("lets someone with a website draft switch to the form instead", async () => {
    // Same cause, second symptom: "set up manually" rewrote the URL and the
    // draft overrode it, so the link did nothing.
    navigation.search = "mode=manual";
    givenReadyDraft();

    render(<GetStartedGate />);

    expect(
      await screen.findByRole("heading", { name: /tell us about your business/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/teach your agent about your business/i)).not.toBeInTheDocument();
  });

  it("offers a way out while the website is being read", async () => {
    // The reading screen used to render no footer, so a customer who had
    // mistyped their address could only wait.
    navigation.search = "mode=website";
    givenNoDraft();
    vi.mocked(api.getWebsiteSetupOrNull).mockResolvedValue({
      id: "job-1",
      status: "crawling",
      website_url: "https://sunset.example.com/",
      attempt: 1,
      last_error: null,
      ready: false,
      tenant_id: null,
      crawl: null,
      knowledge: null,
      choices: null,
      created_at: "2026-10-13T00:00:00Z",
      updated_at: "2026-10-13T00:00:00Z",
    });

    render(<GetStartedGate />);

    expect(await screen.findByText(/reading your website/i)).toBeInTheDocument();
    const back = screen.getByRole("button", { name: /back/i });
    expect(back).toBeInTheDocument();
    // No Next: the screen advances by itself when the crawl finishes.
    expect(screen.queryByRole("button", { name: /^next$/i })).not.toBeInTheDocument();

    fireEvent.click(back);
    expect(await screen.findByLabelText("Your website")).toBeInTheDocument();
  });

  it("goes back from the review to the website field, not to the reading screen", async () => {
    // Back used to be `step - 1`, which landed on a progress screen with
    // nothing left to report — and no footer to escape it.
    navigation.search = "mode=website";
    givenNoDraft();
    vi.mocked(api.getWebsiteSetupOrNull).mockResolvedValue({
      id: "job-1",
      status: "ready",
      website_url: "https://sunset.example.com/",
      attempt: 1,
      last_error: null,
      ready: true,
      tenant_id: null,
      crawl: {
        site: "https://sunset.example.com/",
        pages_read: 3,
        page_urls: [],
        skipped: 0,
        failed: 0,
        duplicates: 0,
        robots_blocked: 0,
        truncated: false,
        reasons: {},
      },
      knowledge: {
        business_name: "Sunset Salon",
        business_summary: "A salon.",
        operating_hours: "Tue-Sat 9-6",
        services: ["Cuts"],
        items: [],
        questions: [],
        sms_templates: [],
      },
      choices: null,
      created_at: "2026-10-13T00:00:00Z",
      updated_at: "2026-10-13T00:00:00Z",
    });

    render(<GetStartedGate />);
    expect(await screen.findByRole("heading", { name: /what your agent learned/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /back/i }));

    expect(await screen.findByLabelText("Your website")).toBeInTheDocument();
    expect(screen.queryByText(/reading your website/i)).not.toBeInTheDocument();
  });
});

describe("the organization switcher", () => {
  it("lists only businesses that are still somewhere to work", async () => {
    // The bug: an account that had deleted two receptionists saw all three in
    // the dropdown, and picking a dead one bounced straight back out again.
    givenTenants({ t1: "cancelled", t2: "cancelled", t3: "active" });
    vi.mocked(api.getProvisioning).mockResolvedValue({
      run_id: "r1",
      tenant_id: "t3",
      status: "active",
      current_step: null,
      attempt: 1,
      last_error: null,
      correlation_id: "corr-1",
      next_attempt_at: null,
      started_at: null,
      finished_at: null,
      steps: [],
      completed_steps: 8,
      total_steps: 8,
      billing: null,
    });
    const threeBusinesses: SessionView = {
      ...OWNER,
      active_tenant_id: "t3",
      memberships: [
        { tenant_id: "t1", name: "Roman Wellness Spa", role: "owner", status: "cancelled" },
        { tenant_id: "t2", name: "Scalezix", role: "owner", status: "cancelled" },
        { tenant_id: "t3", name: "Glocal Studio", role: "owner", status: "active" },
      ],
    };

    renderWorkspace(<p>page content</p>, threeBusinesses);

    expect(await screen.findByText("page content")).toBeInTheDocument();
    // One live business, so no switcher at all — just the business card.
    expect(screen.queryByLabelText("Organization")).not.toBeInTheDocument();
    expect(screen.queryByText("Roman Wellness Spa")).not.toBeInTheDocument();
    expect(screen.queryByText("Scalezix")).not.toBeInTheDocument();
    expect(screen.getAllByText("Glocal Studio").length).toBeGreaterThan(0);
  });

  it("opens the live business even when the session remembers a deleted one", async () => {
    givenTenants({ t1: "cancelled", t3: "active" });
    const staleActive: SessionView = {
      ...OWNER,
      active_tenant_id: "t1",
      memberships: [
        { tenant_id: "t1", name: "Scalezix", role: "owner", status: "cancelled" },
        { tenant_id: "t3", name: "Glocal Studio", role: "owner", status: "active" },
      ],
    };

    renderWorkspace(<p>page content</p>, staleActive);

    await waitFor(() =>
      expect(vi.mocked(api.getTenant).mock.calls.map(([id]) => id)).toContain("t3"),
    );
    expect(vi.mocked(api.getTenant).mock.calls.map(([id]) => id)).not.toContain("t1");
  });
});
