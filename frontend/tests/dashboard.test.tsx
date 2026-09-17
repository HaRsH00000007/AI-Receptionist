/**
 * The customer dashboard: the shell, the workspace that loads a tenant, and
 * the overview page.
 *
 * The property that matters most here is not visual: the dashboard never
 * decides who the viewer is. The tenant id comes from the session's
 * memberships and every request is re-authorized server-side, so these tests
 * check the *consequences* of that — an expired session is handled rather than
 * rendered as an empty account, only member tenants are requested, and nothing
 * tenant-shaped is shown before the data arrives.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AppShell } from "@/components/app/AppShell";
import { TenantWorkspace } from "@/components/app/TenantWorkspace";
import { OverviewView } from "@/components/app/views/OverviewView";
import { ApiError } from "@/lib/api";
import type { CallView, SessionView } from "@/lib/types";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
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
    logout: vi.fn(),
  };
});

const api = await import("@/lib/api");

const SESSION: SessionView = {
  user_id: "u1",
  email: "owner@sunset.example",
  full_name: "Dana Reyes",
  is_platform_admin: false,
  active_tenant_id: "t1",
  memberships: [{ tenant_id: "t1", name: "Sunset Salon", role: "owner" }],
  impersonated: false,
};

const CALL: CallView = {
  id: "call1",
  provider_call_id: "conv1",
  direction: "inbound",
  from_e164: "+15551110000",
  started_at: "2026-09-11T10:00:00Z",
  duration_s: 95,
  status: "notified",
  summary: "Asked about Thursday availability.",
  caller_name: "Chris",
  callback_number: "+15559998888",
  intent: "booking_request",
  urgency: 2,
};

function givenHealthyTenant(usage: Record<string, unknown> = {}, calls: CallView[] = [CALL]) {
  vi.mocked(api.getTenant).mockImplementation(async (tenantId: string) => ({
    id: tenantId,
    name: tenantId === "t2" ? "Careful Legal" : "Sunset Salon",
    business_type: "salon",
    status: "active",
    plan: "starter",
    timezone: "America/Los_Angeles",
    contact_email: "owner@sunset.example",
    area_code: "805",
    created_at: "2026-09-01T00:00:00Z",
  }));
  vi.mocked(api.getProvisioning).mockResolvedValue({
    run_id: "r1",
    tenant_id: "t1",
    status: "active",
    current_step: null,
    attempt: 1,
    last_error: null,
    correlation_id: "c1",
    next_attempt_at: null,
    started_at: null,
    finished_at: null,
    steps: [],
    completed_steps: 8,
    total_steps: 8,
    billing: null,
  });
  vi.mocked(api.getPhoneOrNull).mockResolvedValue({
    e164: "+18055550100",
    status: "active",
    area_code: "805",
    purchased_at: "2026-09-01T00:00:00Z",
    released_at: null,
  });
  vi.mocked(api.getAgentOrNull).mockResolvedValue({
    elevenlabs_agent_id: "agent_1",
    status: "active",
    config_version: 3,
    synced_at: null,
  });
  vi.mocked(api.getUsage).mockResolvedValue({
    period_start: "2026-09-01",
    period_end: "2026-10-01",
    call_count: 12,
    call_minutes: 40,
    included_minutes: 500,
    percent_used: 8,
    over_limit: false,
    warning: false,
    blocks_on_overage: false,
    plan: "starter",
    ...usage,
  });
  vi.mocked(api.listCalls).mockResolvedValue(calls);
  vi.mocked(api.getProfileOrNull).mockResolvedValue({
    services: ["Cuts", "Colour"],
    hours_raw: "Mon-Fri 9-6",
    hours: null,
    greeting_style: "friendly",
    custom_greeting: null,
    escalation_raw: null,
    escalation: null,
    greeting: "Hi, thanks for calling Sunset Salon!",
    config_version: 3,
  });
}

function renderDashboard(session: SessionView = SESSION, onSignedOut: () => void = () => {}) {
  return render(
    <TenantWorkspace session={session} onSignedOut={onSignedOut}>
      <AppShell>
        <OverviewView />
      </AppShell>
    </TenantWorkspace>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  givenHealthyTenant();
});

afterEach(cleanup);

describe("the dashboard", () => {
  it("shows the receptionist's number and its recent calls", async () => {
    renderDashboard();

    expect(await screen.findByText("+1 (805) 555-0100")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Welcome back, Dana" })).toBeInTheDocument();
    expect(screen.getByText("Asked about Thursday availability.")).toBeInTheDocument();
    expect(screen.getAllByText("Sunset Salon").length).toBeGreaterThan(0);
  });

  it("offers a test call to the live number", async () => {
    renderDashboard();
    const link = await screen.findByRole("link", { name: /test receptionist/i });
    expect(link).toHaveAttribute("href", "tel:+18055550100");
  });

  it("shows a loading state before anything arrives", () => {
    renderDashboard();
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  });

  it("marks a callback number as claimed, and never offers to dial it", async () => {
    // The model read it off a transcript. Presenting it as fact — or as a dial
    // link — would invite someone to call a number an attacker chose.
    renderDashboard();
    fireEvent.click(await screen.findByRole("button", { name: /view call from chris/i }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/as stated by the caller/i)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /call back/i })).toHaveAttribute(
      "href",
      "tel:+15551110000",
    );
    expect(dialog.querySelector('a[href="tel:+15559998888"]')).toBeNull();
    expect(within(dialog).getByText(/transcripts and recordings aren.t displayed/i)).toBeInTheDocument();
  });

  it("signs the viewer out when the session has expired", async () => {
    const onSignedOut = vi.fn();
    vi.mocked(api.getTenant).mockRejectedValue(new ApiError("not authenticated", { status: 401 }));

    renderDashboard(SESSION, onSignedOut);

    await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
  });

  it("reports a failure without claiming the account is empty", async () => {
    vi.mocked(api.getTenant).mockRejectedValue(new ApiError("Could not reach the server.", { status: 0 }));

    renderDashboard();

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach the server/i);
    expect(screen.queryByText(/no calls yet/i)).not.toBeInTheDocument();
  });

  it("shows how much of the plan's minutes are used", async () => {
    givenHealthyTenant({ call_minutes: 420, percent_used: 84, warning: true });

    renderDashboard();

    const meter = await screen.findByRole("progressbar");
    expect(meter).toHaveAttribute("aria-valuenow", "84");
    expect(screen.getByText(/close to your included minutes/i)).toBeInTheDocument();
  });

  it("explains an overage differently when the plan blocks", async () => {
    givenHealthyTenant({
      call_minutes: 700,
      percent_used: 140,
      over_limit: true,
      blocks_on_overage: true,
      plan: "trial",
    });

    renderDashboard();

    expect(await screen.findByRole("alert")).toHaveTextContent(/add a payment method/i);
  });

  it("caps the progress bar at 100 percent when over the limit", async () => {
    givenHealthyTenant({ percent_used: 250, over_limit: true });

    renderDashboard();

    const meter = await screen.findByRole("progressbar");
    expect(meter).toHaveAttribute("aria-valuenow", "100");
  });

  it("announces an impersonated session to the person being impersonated", async () => {
    // Support staff viewing a customer account is audited; the customer's own
    // screen saying so is what makes it visible rather than covert.
    renderDashboard({ ...SESSION, impersonated: true });

    expect(await screen.findByText(/as support staff/i)).toBeInTheDocument();
  });

  it("offers an organization switcher only when there is a choice", async () => {
    renderDashboard();
    await screen.findByText("+1 (805) 555-0100");
    expect(screen.queryByLabelText("Organization")).not.toBeInTheDocument();

    cleanup();
    renderDashboard({
      ...SESSION,
      memberships: [
        { tenant_id: "t1", name: "Sunset Salon", role: "owner" },
        { tenant_id: "t2", name: "Careful Legal", role: "member" },
      ],
    });

    const switcher = await screen.findByLabelText("Organization");
    fireEvent.change(switcher, { target: { value: "t2" } });

    await waitFor(() => expect(vi.mocked(api.getTenant)).toHaveBeenCalledWith("t2"));
  });

  it("requests only the tenants the session is a member of", async () => {
    renderDashboard();
    await screen.findByText("+1 (805) 555-0100");

    // The id came from the membership list, never from a URL or user input.
    expect(vi.mocked(api.getTenant)).toHaveBeenCalledWith("t1");
    expect(vi.mocked(api.listCalls)).toHaveBeenCalledWith("t1", undefined, { limit: 100 });
  });

  it("signs out from the account menu", async () => {
    const onSignedOut = vi.fn();
    vi.mocked(api.logout).mockResolvedValue(undefined);

    renderDashboard(SESSION, onSignedOut);
    await screen.findByText("+1 (805) 555-0100");

    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /sign out/i }));

    await waitFor(() => expect(vi.mocked(api.logout)).toHaveBeenCalled());
    await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
  });

  it("does not pretend to sign out when the request fails", async () => {
    // The cookie is still valid server-side; leaving the page would leave a
    // working session behind on a shared computer.
    const onSignedOut = vi.fn();
    vi.mocked(api.logout).mockRejectedValue(new ApiError("offline", { status: 0 }));

    renderDashboard(SESSION, onSignedOut);
    await screen.findByText("+1 (805) 555-0100");

    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /sign out/i }));

    await waitFor(() => expect(vi.mocked(api.logout)).toHaveBeenCalled());
    expect(onSignedOut).not.toHaveBeenCalled();
  });

  it("raises urgent calls as a notification", async () => {
    givenHealthyTenant({}, [{ ...CALL, urgency: 5 }]);

    renderDashboard();
    await screen.findByText("+1 (805) 555-0100");

    fireEvent.click(screen.getByRole("button", { name: /notifications \(1\)/i }));
    expect(await screen.findByRole("menuitem", { name: /1 urgent call/i })).toHaveAttribute(
      "href",
      "/dashboard/calls?urgent=1",
    );
  });

  it("invites a test call when there are no calls yet", async () => {
    givenHealthyTenant({}, []);

    renderDashboard();

    expect(await screen.findByText("No calls yet")).toBeInTheDocument();
  });
});
