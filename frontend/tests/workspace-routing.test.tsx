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
  memberships: [{ tenant_id: "t1", name: "Sunset Salon", role: "owner" }],
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
      memberships: [{ tenant_id: "t1", name: "Sunset Salon", role: "member" }],
    });
    await screen.findByRole("heading", { name: /setup needs attention/i });
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
    expect(screen.getByText(/ask the owner of this account/i)).toBeInTheDocument();
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
    vi.mocked(api.getSessionOrNull).mockResolvedValue({ ...OWNER, memberships: [] });
    vi.mocked(api.getOnboardingDraft).mockRejectedValue(new api.ApiError("down", { status: 0 }));
    vi.mocked(api.saveOnboardingDraft).mockResolvedValue({ data: null, updated_at: null });
    render(<GetStartedGate />);
    expect(await screen.findByLabelText("Business name")).toHaveValue("");
  });

  it("lets a customer deliberately start another business", async () => {
    vi.mocked(api.getSessionOrNull).mockResolvedValue(OWNER);
    vi.mocked(api.getOnboardingDraft).mockResolvedValue({ data: null, updated_at: null });
    navigation.search = "another=1";
    render(<GetStartedGate />);
    expect(await screen.findByLabelText("Business name")).toBeInTheDocument();
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
