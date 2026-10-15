/**
 * The portal's pages: Integrations, SMS, Agent and Contacts.
 *
 * Each page renders what the API says and nothing more. The tests hold the
 * lines that matter to a customer: an integration is only "connected" when the
 * API says so, coming-soon and unconfigured integrations cannot be clicked,
 * SMS campaigns stay locked until SMS is enabled, and only owners and admins
 * are offered the controls that change anything.
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TenantWorkspace } from "@/components/app/TenantWorkspace";
import { AgentView } from "@/components/app/views/AgentView";
import { ContactsView } from "@/components/app/views/ContactsView";
import { IntegrationsView } from "@/components/app/views/IntegrationsView";
import { SmsView } from "@/components/app/views/SmsView";
import { WorkspaceFrame } from "@/components/app/WorkspaceFrame";
import { ToastProvider } from "@/components/ui/Toast";
import type {
  ContactListView,
  ContactView,
  IntegrationView,
  SessionView,
  SmsStateView,
} from "@/lib/types";

const navigation = vi.hoisted(() => ({
  pathname: "/dashboard/integrations",
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
    listIntegrations: vi.fn(),
    connectIntegration: vi.fn(),
    disconnectIntegration: vi.fn(),
    verifyIntegration: vi.fn(),
    getSmsState: vi.fn(),
    saveSmsRegistration: vi.fn(),
    submitSmsRegistration: vi.fn(),
    listConfigVersions: vi.fn(),
    updateAgentSettings: vi.fn(),
    deleteReceptionist: vi.fn(),
    listContacts: vi.fn(),
    createContact: vi.fn(),
    updateContact: vi.fn(),
    deleteContact: vi.fn(),
    setContactBlocked: vi.fn(),
    importContacts: vi.fn(),
    logout: vi.fn(),
  };
});

const api = await import("@/lib/api");

const OWNER: SessionView = {
  user_id: "u1",
  email: "owner@firm.example",
  full_name: "Dana Reyes",
  is_platform_admin: false,
  active_tenant_id: "t1",
  memberships: [{ tenant_id: "t1", name: "Careful Legal", role: "owner", status: "active" }],
  impersonated: false,
};
const MEMBER: SessionView = {
  ...OWNER,
  memberships: [{ tenant_id: "t1", name: "Careful Legal", role: "member", status: "active" }],
};
const ADMIN: SessionView = {
  ...OWNER,
  memberships: [{ tenant_id: "t1", name: "Careful Legal", role: "admin", status: "active" }],
};

function integration(overrides: Partial<IntegrationView>): IntegrationView {
  return {
    id: "google_calendar",
    name: "Google Calendar",
    vendor: "Google",
    category: "calendar",
    description: "Book into your calendar.",
    icon: "calendar-google",
    auth_type: "oauth2",
    capabilities: ["read_availability", "create_event"],
    availability: "available",
    status: "not_connected",
    account: null,
    connected_at: null,
    last_error: null,
    ...overrides,
  };
}

const LEGAL_CATALOG: IntegrationView[] = [
  integration({
    id: "clio",
    name: "Clio",
    vendor: "Clio",
    category: "practice_management",
    icon: "scale",
    capabilities: ["read_calendar", "create_event"],
    availability: "coming_soon",
  }),
  integration({}),
  integration({
    id: "microsoft_outlook",
    name: "Microsoft Outlook",
    vendor: "Microsoft",
    icon: "calendar-outlook",
    availability: "configuration_required",
  }),
  integration({
    id: "zapier",
    name: "Zapier",
    vendor: "Zapier",
    category: "automation",
    icon: "zap",
    auth_type: "webhook",
    capabilities: ["forward_call_events"],
    availability: "coming_soon",
  }),
];

function givenLiveTenant() {
  vi.mocked(api.getTenant).mockResolvedValue({
    id: "t1",
    name: "Careful Legal",
    business_type: "legal",
    status: "active",
    plan: "starter",
    timezone: "America/Los_Angeles",
    contact_email: "owner@firm.example",
    area_code: "805",
    created_at: "2026-09-01T00:00:00Z",
  });
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
    can_release: true,
  });
  vi.mocked(api.getAgentOrNull).mockResolvedValue({
    elevenlabs_agent_id: "agent_1",
    status: "active",
    config_version: 2,
    generated_by: "llm",
    synced_at: "2026-09-02T00:00:00Z",
  });
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
  vi.mocked(api.getProfileOrNull).mockResolvedValue({
    services: ["Consultations", "Wills"],
    hours_raw: "Mon-Fri 9-5",
    hours: null,
    greeting_style: "formal",
    custom_greeting: null,
    escalation_raw: null,
    escalation: null,
    greeting: "Good day, you have reached Careful Legal.",
    config_version: 2,
  });
  vi.mocked(api.listIntegrations).mockResolvedValue({
    business_type: "legal",
    integrations: LEGAL_CATALOG,
  });
  vi.mocked(api.listConfigVersions).mockResolvedValue([
    { version: 2, is_live: true, generated_by: "llm", created_at: "2026-09-02T00:00:00Z" },
    { version: 1, is_live: false, generated_by: "llm", created_at: "2026-09-01T00:00:00Z" },
  ]);
}

function renderPage(page: React.ReactNode, session: SessionView = OWNER) {
  return render(
    <ToastProvider>
      <TenantWorkspace session={session} onSignedOut={() => {}}>
        <WorkspaceFrame>{page}</WorkspaceFrame>
      </TenantWorkspace>
    </ToastProvider>,
  );
}

function card(name: string): HTMLElement {
  const heading = screen.getByRole("heading", { name });
  const element = heading.closest(".card");
  if (!(element instanceof HTMLElement)) throw new Error(`no card for ${name}`);
  return element;
}

beforeEach(() => {
  vi.clearAllMocks();
  navigation.search = "";
  givenLiveTenant();
});

afterEach(cleanup);

// ---------------------------------------------------------------------------
// Integrations
// ---------------------------------------------------------------------------

describe("Integrations", () => {
  it("shows the vertical's integrations with honest states", async () => {
    navigation.pathname = "/dashboard/integrations";
    renderPage(<IntegrationsView />);

    await screen.findByRole("heading", { name: "Clio" });
    expect(within(card("Clio")).getByText("Coming soon")).toBeInTheDocument();
    expect(within(card("Clio")).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card("Microsoft Outlook")).getByText("Configuration required")).toBeInTheDocument();
    expect(within(card("Microsoft Outlook")).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card("Google Calendar")).getByRole("button", { name: "Connect" })).toBeEnabled();
    // Practice management first for a law firm: its own system leads.
    const headings = screen.getAllByRole("heading", { level: 2 }).map((node) => node.textContent);
    expect(headings.slice(0, 3)).toEqual(["Practice management", "Calendars", "Automation"]);
  });

  it("sends the owner to the provider's consent screen to connect", async () => {
    navigation.pathname = "/dashboard/integrations";
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    vi.mocked(api.connectIntegration).mockResolvedValue({
      authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?state=abc",
    });
    renderPage(<IntegrationsView />);

    fireEvent.click(
      within(await waitFor(() => card("Google Calendar"))).getByRole("button", { name: "Connect" }),
    );
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?state=abc"),
    );
    expect(api.connectIntegration).toHaveBeenCalledWith("t1", "google_calendar");
    vi.unstubAllGlobals();
  });

  it("shows a connection only when the API reports one", async () => {
    navigation.pathname = "/dashboard/integrations";
    vi.mocked(api.listIntegrations).mockResolvedValue({
      business_type: "legal",
      integrations: [
        integration({
          status: "connected",
          account: "owner@gmail.example.com",
          connected_at: "2026-09-10T00:00:00Z",
        }),
      ],
    });
    vi.mocked(api.verifyIntegration).mockResolvedValue({
      ok: true,
      window_start: "2026-09-22T00:00:00Z",
      window_end: "2026-09-29T00:00:00Z",
      busy_blocks: 3,
    });
    renderPage(<IntegrationsView />);

    const google = await waitFor(() => card("Google Calendar"));
    expect(within(google).getByText("Connected")).toBeInTheDocument();
    expect(within(google).getByText(/owner@gmail\.example\.com/)).toBeInTheDocument();
    fireEvent.click(within(google).getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/3 busy blocks in the next 7 days/i)).toBeInTheDocument();
  });

  it("offers a member no controls", async () => {
    navigation.pathname = "/dashboard/integrations";
    renderPage(<IntegrationsView />, MEMBER);
    await screen.findByRole("heading", { name: "Google Calendar" });
    expect(screen.getByText(/only the account.s owners and admins/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
  });

  it("explains a failed return from the consent screen", async () => {
    navigation.pathname = "/dashboard/integrations";
    navigation.search = "integration=google_calendar&integration_error=access_denied";
    renderPage(<IntegrationsView />);
    expect(await screen.findByText(/cancelled on the consent screen/i)).toBeInTheDocument();
    expect(navigation.replace).toHaveBeenCalledWith("/dashboard/integrations");
  });
});

// ---------------------------------------------------------------------------
// SMS
// ---------------------------------------------------------------------------

function smsState(overrides: Partial<SmsStateView>): SmsStateView {
  return {
    state: "compliance_required",
    phone_number: "+18055550100",
    can_send: false,
    editable: true,
    registration: null,
    ...overrides,
  };
}

describe("SMS", () => {
  beforeEach(() => {
    navigation.pathname = "/dashboard/sms";
  });

  it("asks for registration before anything can be sent", async () => {
    vi.mocked(api.getSmsState).mockResolvedValue(smsState({}));
    renderPage(<SmsView />);

    expect(await screen.findByText("Registration required")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new campaign/i })).toBeDisabled();
    expect(screen.getByText(/campaigns unlock once sms is enabled/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /start registration/i }));
    expect(await screen.findByLabelText("Legal business name")).toBeInTheDocument();
  });

  it("names what is missing when a submission is incomplete", async () => {
    vi.mocked(api.getSmsState).mockResolvedValue(smsState({}));
    vi.mocked(api.saveSmsRegistration).mockResolvedValue(smsState({ state: "draft" }));
    vi.mocked(api.submitSmsRegistration).mockRejectedValue(
      new api.ApiError("some details are missing", {
        status: 422,
        code: "sms_registration_incomplete",
        details: { missing: ["tax_id", "sample_messages"] },
      }),
    );
    renderPage(<SmsView />);
    fireEvent.click(await screen.findByRole("button", { name: /start registration/i }));
    fireEvent.click(await screen.findByRole("button", { name: /submit for review/i }));

    expect(await screen.findByText(/still needed: ein; two sample messages/i)).toBeInTheDocument();
  });

  it("reports review without offering to edit it", async () => {
    vi.mocked(api.getSmsState).mockResolvedValue(
      smsState({
        state: "under_review",
        editable: false,
        registration: {
          status: "under_review",
          brand_type: "standard",
          legal_business_name: "Careful Legal LLP",
          tax_id: "12-3456789",
          website: "https://careful.example",
          address_line1: "1 Main St",
          address_line2: null,
          city: "Santa Barbara",
          region: "CA",
          postal_code: "93101",
          country: "US",
          contact_name: "Dana",
          contact_email: "dana@careful.example",
          contact_phone: "+18055550142",
          use_case: "customer_care",
          campaign_description: "Replies to people who called about a consultation.",
          sample_messages: ["a", "b"],
          opt_in_description: "Callers agree on the phone.",
          rejection_reason: null,
          submitted_at: "2026-09-20T00:00:00Z",
          approved_at: null,
          enabled_at: null,
        },
      }),
    );
    renderPage(<SmsView />);
    expect(await screen.findByText("Under review")).toBeInTheDocument();
    expect(screen.getByText("Careful Legal LLP")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit for review/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new campaign/i })).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// Agent
// ---------------------------------------------------------------------------

describe("Agent", () => {
  beforeEach(() => {
    navigation.pathname = "/dashboard/agent";
  });

  it("publishes a new version with the owner's edits", async () => {
    vi.mocked(api.updateAgentSettings).mockResolvedValue({
      config_version: 3,
      previous_version: 2,
      generated_by: "llm",
      synced: true,
      detail: "Saved. Your receptionist uses it from the next call.",
    });
    renderPage(<AgentView />);

    fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
    fireEvent.change(screen.getByLabelText("Operating hours"), { target: { value: "Mon-Sat 9-6" } });
    fireEvent.click(screen.getByRole("button", { name: /save and publish/i }));

    await waitFor(() =>
      expect(api.updateAgentSettings).toHaveBeenCalledWith("t1", {
        services: "Consultations, Wills",
        operating_hours: "Mon-Sat 9-6",
        greeting_style: "formal",
        custom_greeting: "",
        escalation_rules: "",
      }),
    );
    expect(await screen.findByText("Version 3 is live")).toBeInTheDocument();
  });

  it("refuses to publish without services or hours", async () => {
    renderPage(<AgentView />);
    fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
    fireEvent.change(screen.getByLabelText("Operating hours"), { target: { value: " " } });
    fireEvent.click(screen.getByRole("button", { name: /save and publish/i }));
    expect(await screen.findByText(/tell callers when you.re open/i)).toBeInTheDocument();
    expect(api.updateAgentSettings).not.toHaveBeenCalled();
  });

  it("is read-only for a member", async () => {
    renderPage(<AgentView />, MEMBER);
    expect(await screen.findByText("Version history")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^edit$/i })).not.toBeInTheDocument();
    expect(screen.getByText(/only the account.s owners and admins/i)).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Deleting the receptionist
  // -------------------------------------------------------------------------

  it("keeps the phone number unless the owner ticks the box", async () => {
    vi.mocked(api.deleteReceptionist).mockResolvedValue({
      complete: true,
      tenant_status: "cancelled",
      agent_deleted: true,
      shared_agent_detached: false,
      number_released: false,
      number_retained: "+18055550100",
      release_refused: null,
      errors: [],
    });
    renderPage(<AgentView />);

    fireEvent.click(await screen.findByRole("button", { name: /delete receptionist/i }));
    const dialog = await screen.findByRole("dialog");
    // The dialog names what is being deleted and the number at stake.
    expect(within(dialog).getByText("Careful Legal")).toBeInTheDocument();
    expect(within(dialog).getByText("+1 (805) 555-0100")).toBeInTheDocument();
    expect(within(dialog).getByText(/no longer in service/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/rental continues/i)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /delete receptionist/i }));

    await waitFor(() => expect(api.deleteReceptionist).toHaveBeenCalledWith("t1", false));
    expect(await screen.findByText("Your receptionist was deleted")).toBeInTheDocument();
    // Onto the setup form, not the dead-end page for a cancelled business.
    expect(navigation.push).toHaveBeenCalledWith("/get-started?another=1");
  });

  it("releases the number only when the box is ticked, after warning", async () => {
    vi.mocked(api.deleteReceptionist).mockResolvedValue({
      complete: true,
      tenant_status: "cancelled",
      agent_deleted: true,
      shared_agent_detached: false,
      number_released: true,
      number_retained: null,
      release_refused: null,
      errors: [],
    });
    renderPage(<AgentView />);

    fireEvent.click(await screen.findByRole("button", { name: /delete receptionist/i }));
    const dialog = await screen.findByRole("dialog");
    const release = within(dialog).getByRole("checkbox");
    expect(within(dialog).getByText(/cannot be recovered/i)).toBeInTheDocument();
    fireEvent.click(release);
    fireEvent.click(within(dialog).getByRole("button", { name: /delete receptionist/i }));

    await waitFor(() => expect(api.deleteReceptionist).toHaveBeenCalledWith("t1", true));
    expect(await screen.findByText(/phone number has been released/i)).toBeInTheDocument();
  });

  it("does not offer to release a number we did not buy", async () => {
    vi.mocked(api.getPhoneOrNull).mockResolvedValue({
      e164: "+18055550100",
      status: "active",
      area_code: "805",
      purchased_at: "2026-09-01T00:00:00Z",
      released_at: null,
      can_release: false,
    });
    renderPage(<AgentView />);

    fireEvent.click(await screen.findByRole("button", { name: /delete receptionist/i }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(dialog).getByText(/not bought through us/i)).toBeInTheDocument();
  });

  it("does not claim success when vendor cleanup failed", async () => {
    vi.mocked(api.deleteReceptionist).mockResolvedValue({
      complete: false,
      tenant_status: "cancelled",
      agent_deleted: false,
      shared_agent_detached: false,
      number_released: false,
      number_retained: "+18055550100",
      release_refused: null,
      errors: ["release_number: vendor_error"],
    });
    renderPage(<AgentView />);

    fireEvent.click(await screen.findByRole("button", { name: /delete receptionist/i }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: /delete receptionist/i,
      }),
    );

    expect(await screen.findByText("Cleanup unfinished")).toBeInTheDocument();
    expect(screen.getByText(/could not be released/i)).toBeInTheDocument();
    expect(screen.queryByText("Your receptionist was deleted")).not.toBeInTheDocument();
    // Stays put: the number is still billing and the retry is on this page.
    expect(navigation.push).not.toHaveBeenCalled();
  });

  it("sends one delete however often the button is clicked", async () => {
    let resolve: (value: never) => void = () => {};
    vi.mocked(api.deleteReceptionist).mockImplementation(
      () => new Promise((settle) => {
        resolve = settle as (value: never) => void;
      }),
    );
    renderPage(<AgentView />);

    fireEvent.click(await screen.findByRole("button", { name: /delete receptionist/i }));
    const confirm = within(await screen.findByRole("dialog")).getByRole("button", {
      name: /delete receptionist/i,
    });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    expect(api.deleteReceptionist).toHaveBeenCalledTimes(1);
    resolve(undefined as never);
  });

  it("offers deletion to the owner alone", async () => {
    renderPage(<AgentView />, ADMIN);
    expect(await screen.findByText("Version history")).toBeInTheDocument();
    // An admin may edit the receptionist, but not end the service.
    expect(screen.getByRole("button", { name: /^edit$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete receptionist/i })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Contacts
// ---------------------------------------------------------------------------

describe("Contacts", () => {
  function contact(overrides: Partial<ContactView>): ContactView {
    return {
      id: "c1",
      phone_e164: "+15551110001",
      full_name: "Chris Patel",
      note: "Wants a consultation",
      status: "active",
      source: "call",
      created_at: "2026-09-01T10:00:00Z",
      blocked_at: null,
      call_count: 2,
      first_call_at: "2026-09-01T10:00:00Z",
      last_call_at: "2026-09-20T10:00:00Z",
      ...overrides,
    };
  }

  function page(contacts: ContactView[], counts: Partial<ContactListView> = {}): ContactListView {
    return {
      contacts,
      total: contacts.length,
      active_count: contacts.filter((entry) => entry.status === "active").length,
      blocked_count: contacts.filter((entry) => entry.status === "blocked").length,
      limit: 25,
      offset: 0,
      ...counts,
    };
  }

  beforeEach(() => {
    navigation.pathname = "/dashboard/contacts";
    vi.mocked(api.createContact).mockResolvedValue(contact({}));
    vi.mocked(api.updateContact).mockResolvedValue(contact({}));
    vi.mocked(api.deleteContact).mockResolvedValue(undefined);
    vi.mocked(api.setContactBlocked).mockResolvedValue(contact({ status: "blocked" }));
  });

  it("says so when nobody has called yet", async () => {
    vi.mocked(api.listContacts).mockResolvedValue(page([]));

    renderPage(<ContactsView />);

    expect(await screen.findByText("No contacts yet")).toBeInTheDocument();
    expect(
      screen.getByText("Contacts captured from completed calls will appear here."),
    ).toBeInTheDocument();
  });

  it("shows a contact with its note and call count", async () => {
    vi.mocked(api.listContacts).mockResolvedValue(page([contact({})]));

    renderPage(<ContactsView />);

    expect(await screen.findByText("Chris Patel")).toBeInTheDocument();
    expect(screen.getByText("+1 (555) 111-0001")).toBeInTheDocument();
    expect(screen.getByText("Wants a consultation")).toBeInTheDocument();
  });

  it("calls a caller with no name an unknown caller, rather than guessing one", async () => {
    vi.mocked(api.listContacts).mockResolvedValue(
      page([contact({ full_name: null, note: null })]),
    );

    renderPage(<ContactsView />);

    expect(await screen.findByText("Unknown caller")).toBeInTheDocument();
  });

  it("says so when contacts cannot be loaded, and offers a retry", async () => {
    vi.mocked(api.listContacts).mockRejectedValue(new api.ApiError("down", { status: 0 }));

    renderPage(<ContactsView />);

    expect(await screen.findByText("Contacts couldn't be loaded")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("adds a contact", async () => {
    vi.mocked(api.listContacts).mockResolvedValue(page([]));
    renderPage(<ContactsView />);

    fireEvent.click(await screen.findByRole("button", { name: /add contact/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/phone number/i), {
      target: { value: "(805) 555-0142" },
    });
    fireEvent.change(within(dialog).getByLabelText(/full name/i), {
      target: { value: "Dana Rivera" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /^add contact$/i }));

    await waitFor(() =>
      expect(api.createContact).toHaveBeenCalledWith("t1", {
        phone_number: "(805) 555-0142",
        full_name: "Dana Rivera",
        note: "",
      }),
    );
  });

  it("refuses to send an obviously invalid phone number", async () => {
    vi.mocked(api.listContacts).mockResolvedValue(page([]));
    renderPage(<ContactsView />);

    fireEvent.click(await screen.findByRole("button", { name: /add contact/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/phone number/i), { target: { value: "123" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^add contact$/i }));

    expect(await within(dialog).findByText(/10-digit US phone number/i)).toBeInTheDocument();
    expect(api.createContact).not.toHaveBeenCalled();
  });

  it("removes a contact, and says the call history is kept", async () => {
    vi.mocked(api.listContacts).mockResolvedValue(page([contact({})]));
    renderPage(<ContactsView />);

    fireEvent.click(await screen.findByRole("button", { name: /actions for chris patel/i }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /remove/i }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/transcripts and summaries are kept/i)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /remove contact/i }));

    await waitFor(() => expect(api.deleteContact).toHaveBeenCalledWith("t1", "c1"));
  });

  it("blocks a contact without implying their calls stop", async () => {
    vi.mocked(api.listContacts).mockResolvedValue(page([contact({})]));
    renderPage(<ContactsView />);

    fireEvent.click(await screen.findByRole("button", { name: /actions for chris patel/i }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /^block$/i }));

    await waitFor(() => expect(api.setContactBlocked).toHaveBeenCalledWith("t1", "c1", true));
    expect(await screen.findByText(/calls are still answered/i)).toBeInTheDocument();
  });

  it("switches to the blocked tab", async () => {
    vi.mocked(api.listContacts).mockResolvedValue(
      page([contact({})], { active_count: 1, blocked_count: 3 }),
    );
    renderPage(<ContactsView />);

    fireEvent.click(await screen.findByRole("tab", { name: /blocked \(3\)/i }));

    await waitFor(() =>
      expect(api.listContacts).toHaveBeenCalledWith("t1", {
        search: "",
        status: "blocked",
        limit: 25,
        offset: 0,
      }),
    );
  });

  it("reports every row an import could not read", async () => {
    vi.mocked(api.listContacts).mockResolvedValue(page([]));
    vi.mocked(api.importContacts).mockResolvedValue({
      imported: 2,
      updated: 1,
      skipped_duplicate: 1,
      invalid: 2,
      errors: [
        { row: 4, reason: "phone number must have 10 digits" },
        { row: 7, reason: "no phone number" },
      ],
    });
    renderPage(<ContactsView />);

    fireEvent.click(await screen.findByRole("button", { name: /import csv/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/csv file/i), {
      target: { files: [new File(["name,phone"], "contacts.csv")] },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /^import$/i }));

    expect(await screen.findByText("Import finished")).toBeInTheDocument();
    expect(screen.getByText("Row 4")).toBeInTheDocument();
    expect(screen.getByText("phone number must have 10 digits")).toBeInTheDocument();
  });

  it("offers no editing controls to a member", async () => {
    vi.mocked(api.listContacts).mockResolvedValue(page([contact({})]));

    renderPage(<ContactsView />, MEMBER);

    expect(await screen.findByText("Chris Patel")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add contact/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /import csv/i })).not.toBeInTheDocument();
  });
});
