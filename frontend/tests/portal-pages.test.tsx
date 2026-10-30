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
import { AgentGeneral } from "@/components/app/agent/AgentGeneral";
import { AgentGreetings } from "@/components/app/agent/AgentGreetings";
import { AgentTags } from "@/components/app/agent/AgentTags";
import { AgentTransfers } from "@/components/app/agent/AgentTransfers";
import { ReceptionistsView } from "@/components/app/views/ReceptionistsView";
import { AgentView } from "@/components/app/views/AgentView";
import { ContactsView } from "@/components/app/views/ContactsView";
import { IntegrationsView } from "@/components/app/views/IntegrationsView";
import { SmsView } from "@/components/app/views/SmsView";
import { WorkspaceFrame } from "@/components/app/WorkspaceFrame";
import { ToastProvider } from "@/components/ui/Toast";
import type {
  ContactListView,
  ContactView,
  GreetingRuleResult,
  GreetingRuleView,
  CallTagsView,
  CallTagView,
  ReceptionistCardView,
  VoiceOptionView,
  IntegrationView,
  SessionView,
  SmsStateView,
  TransferRuleResult,
  TransferRuleView,
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
    listTimezones: vi.fn(),
    listAgentVoices: vi.fn(),
    listCallTags: vi.fn(),
    createCallTag: vi.fn(),
    updateCallTag: vi.fn(),
    deleteCallTag: vi.fn(),
    listReceptionists: vi.fn(),
    listGreetingRules: vi.fn(),
    createGreetingRule: vi.fn(),
    updateGreetingRule: vi.fn(),
    deleteGreetingRule: vi.fn(),
    listTransferRules: vi.fn(),
    createTransferRule: vi.fn(),
    updateTransferRule: vi.fn(),
    deleteTransferRule: vi.fn(),
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
    agent_name: null,
    services: ["Consultations", "Wills"],
    hours_raw: "Mon-Fri 9-5",
    hours: null,
    greeting_style: "formal",
    custom_greeting: null,
      custom_goodbye: null,
      voice_id: null,
    speaking_speed: null,
    responsiveness: null,
    escalation_raw: null,
    escalation: null,
    greeting: "Good day, you have reached Careful Legal.",
    config_version: 2,
  });
  vi.mocked(api.listIntegrations).mockResolvedValue({
    business_type: "legal",
    integrations: LEGAL_CATALOG,
  });
  vi.mocked(api.listTimezones).mockResolvedValue([
    { timezone: "America/New_York", label: "Eastern Time" },
    { timezone: "America/Chicago", label: "Central Time" },
    { timezone: "America/Los_Angeles", label: "Pacific Time" },
  ]);
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

// ---------------------------------------------------------------------------
// Agent -> General
// ---------------------------------------------------------------------------

describe("Agent / General", () => {
  beforeEach(() => {
    navigation.pathname = "/dashboard/agent";
    vi.mocked(api.updateAgentSettings).mockResolvedValue({
      config_version: 2,
      previous_version: null,
      generated_by: "llm",
      synced: true,
      detail: "Saved. Nothing your receptionist says has changed, so there was no new version to publish.",
    });
  });

  it("sends the rest of the configuration untouched when only the name changes", async () => {
    renderPage(<AgentGeneral />);

    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Hope" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() =>
      expect(api.updateAgentSettings).toHaveBeenCalledWith("t1", {
        services: "Consultations, Wills",
        operating_hours: "Mon-Fri 9-5",
        greeting_style: "formal",
        custom_greeting: "",
        // Sent as it stands, not omitted: this panel owns the field, and "" on
        // a profile that never had a sign-off means the same as leaving it.
        custom_goodbye: "",
        escalation_rules: "",
        agent_name: "Hope",
      }),
    );
    // No timezone key at all: an unchanged zone must not look like an edit.
    const sent = vi.mocked(api.updateAgentSettings).mock.calls[0]![1];
    expect("timezone" in sent).toBe(false);
  });

  it("warns before a timezone change, because it republishes", async () => {
    renderPage(<AgentGeneral />);

    expect(screen.queryByText(/republishes your receptionist/i)).not.toBeInTheDocument();
    fireEvent.change(await screen.findByLabelText("Time zone"), {
      target: { value: "America/New_York" },
    });

    expect(screen.getByText(/republishes your receptionist/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() =>
      expect(vi.mocked(api.updateAgentSettings).mock.calls[0]![1].timezone).toBe(
        "America/New_York",
      ),
    );
  });

  it("offers only the timezones the server sent", async () => {
    renderPage(<AgentGeneral />);

    const select = await screen.findByLabelText("Time zone");
    const values = within(select).getAllByRole("option").map((o) => o.getAttribute("value"));
    expect(values).toEqual(["America/New_York", "America/Chicago", "America/Los_Angeles"]);
  });

  it("cannot save until something changes", async () => {
    renderPage(<AgentGeneral />);

    const save = await screen.findByRole("button", { name: /save changes/i });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Hope" } });
    expect(save).toBeEnabled();
  });

  it("shows a member the settings but not the controls", async () => {
    renderPage(<AgentGeneral />, MEMBER);

    expect(await screen.findByLabelText("Name")).toBeDisabled();
    expect(screen.queryByRole("button", { name: /save changes/i })).not.toBeInTheDocument();
    expect(screen.getByText(/only an owner or admin/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Agent / Transfer calls
// ---------------------------------------------------------------------------

function transferRule(overrides: Partial<TransferRuleView> = {}): TransferRuleView {
  return {
    id: "tr1",
    label: "Front desk",
    destination_e164: "+18055550143",
    condition: "the caller asks about billing",
    transfer_type: "warm",
    enabled: true,
    position: 1,
    created_at: "2026-10-20T00:00:00Z",
    ...overrides,
  };
}

function transferResult(
  rules: TransferRuleView[],
  overrides: Partial<TransferRuleResult> = {},
): TransferRuleResult {
  return {
    rules,
    max_rules: 10,
    cold_transfer_available: true,
    config_version: 3,
    synced: true,
    detail: "Saved. Your receptionist uses it from the next call.",
    ...overrides,
  };
}

/** No rules yet. `coldAvailable` is what the call path can actually do. */
function givenNoTransferRules(coldAvailable = true) {
  vi.mocked(api.listTransferRules).mockResolvedValue({
    rules: [],
    max_rules: 10,
    cold_transfer_available: coldAvailable,
  });
}

function givenOneTransferRule() {
  vi.mocked(api.listTransferRules).mockResolvedValue({
    rules: [transferRule()],
    max_rules: 10,
    cold_transfer_available: true,
  });
}

describe("Agent / Transfer calls", () => {
  beforeEach(() => {
    navigation.pathname = "/dashboard/agent";
    navigation.search = "?section=transfer";
  });

  it("asks for a rule when there are none, the way the empty state should", async () => {
    givenNoTransferRules();
    renderPage(<AgentTransfers />);

    expect(
      await screen.findByText(/want certain calls to go to a real person\?/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add transferring rule/i })).toBeEnabled();
  });

  it("sends the number and the condition, and shows what publishing did", async () => {
    givenNoTransferRules();
    vi.mocked(api.createTransferRule).mockResolvedValue(transferResult([transferRule()]));
    renderPage(<AgentTransfers />);

    fireEvent.click(await screen.findByRole("button", { name: /add transferring rule/i }));
    fireEvent.change(screen.getByLabelText("Transfer to"), { target: { value: "(805) 555-0143" } });
    fireEvent.change(screen.getByLabelText(/when should calls be transferred/i), {
      target: { value: "the caller asks about billing" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add rule/i }));

    await waitFor(() =>
      expect(api.createTransferRule).toHaveBeenCalledWith("t1", {
        destination_number: "(805) 555-0143",
        condition: "the caller asks about billing",
        label: "",
        transfer_type: "warm",
      }),
    );
    // The write answers with the whole list, so the page needs no second read.
    expect(await screen.findByText(/\(805\) 555-0143/)).toBeInTheDocument();
    expect(api.listTransferRules).toHaveBeenCalledTimes(1);
  });

  it("will not send a rule with no condition", async () => {
    givenNoTransferRules();
    renderPage(<AgentTransfers />);

    fireEvent.click(await screen.findByRole("button", { name: /add transferring rule/i }));
    fireEvent.change(screen.getByLabelText("Transfer to"), { target: { value: "8055550143" } });
    fireEvent.click(screen.getByRole("button", { name: /add rule/i }));

    expect(
      await screen.findByText(/say when this rule should transfer a call/i),
    ).toBeInTheDocument();
    expect(api.createTransferRule).not.toHaveBeenCalled();
  });

  it("says so when the rule is saved but the voice agent was not updated", async () => {
    givenOneTransferRule();
    vi.mocked(api.deleteTransferRule).mockResolvedValue(
      transferResult([], {
        synced: false,
        detail:
          "Saved, but the voice agent couldn't be updated yet, so calls are still being " +
          "transferred the previous way.",
      }),
    );
    renderPage(<AgentTransfers />);

    fireEvent.click(await screen.findByRole("button", { name: /actions for front desk/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete rule/i }));
    fireEvent.click(screen.getByRole("button", { name: /^delete rule$/i }));

    // On the page, not only in the toast: a deleted rule that has not reached
    // the vendor still transfers callers, and a message that fades away hides
    // exactly the thing the owner needs to act on.
    const warnings = await screen.findAllByText(/still being transferred the previous way/i);
    expect(warnings.some((node) => node.closest(".alert"))).toBe(true);
  });

  it("offers only an announced transfer where a straight-through one cannot work", async () => {
    givenNoTransferRules(false);
    renderPage(<AgentTransfers />);

    fireEvent.click(await screen.findByRole("button", { name: /add transferring rule/i }));
    expect(screen.queryByLabelText(/straight through/i)).not.toBeInTheDocument();
    expect(screen.getByText(/transferred with an announcement/i)).toBeInTheDocument();
  });

  it("shows a member the rules but none of the controls", async () => {
    givenOneTransferRule();
    renderPage(<AgentTransfers />, MEMBER);

    expect(await screen.findByText("Front desk")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /add transferring rule/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /actions for/i })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Agent / General — scheduled greetings
// ---------------------------------------------------------------------------

const USUAL = "Thank you for calling Careful Legal. How can I help you today?";
const LUNCH = "We're closed for lunch until one. Leave a message and we'll call you back.";

function greetingRule(overrides: Partial<GreetingRuleView> = {}): GreetingRuleView {
  return {
    id: "gr1",
    kind: "weekly",
    message: LUNCH,
    weekdays: [0, 1, 2, 3, 4],
    start_minute: 720,
    end_minute: 780,
    start_date: null,
    end_date: null,
    enabled: true,
    position: 1,
    created_at: "2026-10-20T00:00:00Z",
    ...overrides,
  };
}

function greetingResult(
  rules: GreetingRuleView[],
  overrides: Partial<GreetingRuleResult> = {},
): GreetingRuleResult {
  return {
    rules,
    max_rules: 24,
    timezone: "America/New_York",
    default_greeting: USUAL,
    active_message: USUAL,
    config_version: 4,
    synced: true,
    detail: "Saved. Your receptionist uses it from the next call.",
    ...overrides,
  };
}

function givenGreetings(rules: GreetingRuleView[], overrides: Partial<GreetingRuleResult> = {}) {
  const { config_version, synced, detail, ...view } = greetingResult(rules, overrides);
  void config_version;
  void synced;
  void detail;
  vi.mocked(api.listGreetingRules).mockResolvedValue(view);
}

describe("Agent / General — scheduled greetings", () => {
  beforeEach(() => {
    navigation.pathname = "/dashboard/agent";
    navigation.search = "?section=general";
  });

  it("answers what callers hear right now, not just when the rules apply", async () => {
    givenGreetings([greetingRule()], { active_message: LUNCH });
    renderPage(<AgentGreetings />);

    expect(await screen.findByText(/right now, callers hear/i)).toBeInTheDocument();
    // Twice: once as the line in force, once as the rule that chose it.
    expect(screen.getAllByText(`“${LUNCH}”`)).toHaveLength(2);
    // And it says the exception is temporary, so nobody edits it back by hand.
    expect(screen.getByText(/comes back on its own/i)).toBeInTheDocument();
  });

  it("names the clock the rules are read against", async () => {
    givenGreetings([]);
    renderPage(<AgentGreetings />);

    // Once per card. Assuming your own timezone is the mistake this prevents.
    expect(await screen.findAllByText("America/New York")).toHaveLength(2);
  });

  it("sends the days and the window a weekly greeting was given", async () => {
    givenGreetings([]);
    vi.mocked(api.createGreetingRule).mockResolvedValue(greetingResult([greetingRule()]));
    renderPage(<AgentGreetings />);

    const weekly = (await screen.findByText(/different greeting at certain times/i)).closest(
      ".card",
    )!;
    fireEvent.click(within(weekly as HTMLElement).getByRole("button", { name: /add rule/i }));
    fireEvent.change(screen.getByLabelText("Greeting message"), { target: { value: LUNCH } });
    // Scoped to the dialog: the cards have "Add rule" buttons of their own.
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: /^add rule$/i }),
    );

    await waitFor(() =>
      expect(api.createGreetingRule).toHaveBeenCalledWith("t1", {
        kind: "weekly",
        message: LUNCH,
        weekdays: [0, 1, 2, 3, 4],
        start_minute: 720,
        end_minute: 780,
      }),
    );
  });

  it("will not send a dated greeting with no date", async () => {
    givenGreetings([]);
    renderPage(<AgentGreetings />);

    const dated = (await screen.findByText(/different greeting on specific dates/i)).closest(
      ".card",
    )!;
    fireEvent.click(within(dated as HTMLElement).getByRole("button", { name: /add rule/i }));
    fireEvent.change(screen.getByLabelText("Greeting message"), { target: { value: "Closed." } });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: /^add rule$/i }),
    );

    expect(await screen.findByText(/choose the date this greeting applies to/i)).toBeInTheDocument();
    expect(api.createGreetingRule).not.toHaveBeenCalled();
  });

  it("says so when the greeting is saved but the voice agent was not updated", async () => {
    givenGreetings([greetingRule()]);
    vi.mocked(api.deleteGreetingRule).mockResolvedValue(
      greetingResult([], {
        synced: false,
        detail:
          "Saved, but the voice agent couldn't be updated yet, so callers still hear the " +
          "previous greeting.",
      }),
    );
    renderPage(<AgentGreetings />);

    fireEvent.click(await screen.findByRole("button", { name: /actions for the greeting/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete greeting/i }));
    fireEvent.click(screen.getByRole("button", { name: /^delete greeting$/i }));

    // On the page, not only in a toast: until it syncs, callers are hearing
    // something the owner has already deleted.
    const warnings = await screen.findAllByText(/still hear the previous greeting/i);
    expect(warnings.some((node) => node.closest(".alert"))).toBe(true);
  });

  it("keeps a switched-off greeting on file and out of the schedule", async () => {
    givenGreetings([greetingRule({ enabled: false })]);
    renderPage(<AgentGreetings />);

    expect(await screen.findByText("Off")).toBeInTheDocument();
    expect(screen.getByText(`“${LUNCH}”`)).toBeInTheDocument();
  });

  it("shows a member the schedule but none of the controls", async () => {
    givenGreetings([greetingRule()]);
    renderPage(<AgentGreetings />, MEMBER);

    expect(await screen.findByText(`“${LUNCH}”`)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add rule/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /actions for/i })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Agent / General — the voice
// ---------------------------------------------------------------------------

function voice(overrides: Partial<VoiceOptionView> = {}): VoiceOptionView {
  return {
    voice_id: "v-sarah",
    label: "Sarah",
    style: "professional",
    summary: "American · Mature, Reassuring, Confident",
    description: "Young adult woman with a confident and warm, mature quality.",
    accent: "American",
    gender: "female",
    age: "young",
    language: "en",
    preview_url: "https://example.invalid/sarah.mp3",
    recommended: true,
    avatar: "ada",
    ...overrides,
  };
}

/** Six recommended and one behind the fold, like a real catalogue. */
function givenVoices(extra: VoiceOptionView[] = []) {
  const recommended = ["Sarah", "Jessica", "Tiffany", "Finn", "Mark", "Eric"].map((name) =>
    voice({ voice_id: `v-${name.toLowerCase()}`, label: name }),
  );
  vi.mocked(api.listAgentVoices).mockResolvedValue([...recommended, ...extra]);
}

describe("Agent / General — the voice", () => {
  beforeEach(() => {
    navigation.pathname = "/dashboard/agent";
    navigation.search = "?section=general";
    vi.mocked(api.updateAgentSettings).mockResolvedValue({
      config_version: 3,
      previous_version: 2,
      generated_by: "llm",
      synced: true,
      detail: "Saved. Your receptionist uses it from the next call.",
    });
  });

  it("shows the recommended voices and keeps the rest one click away", async () => {
    givenVoices([voice({ voice_id: "v-brian", label: "Brian", recommended: false })]);
    renderPage(<AgentGeneral />);

    expect(await screen.findByText("Sarah")).toBeInTheDocument();
    // Thirty voices is not a choice; the long tail waits until it is asked for.
    expect(screen.queryByText("Brian")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /show 1 more/i }));

    expect(screen.getByText("Brian")).toBeInTheDocument();
  });

  it("sends the chosen voice, and only when it changed", async () => {
    givenVoices();
    renderPage(<AgentGeneral />);

    fireEvent.click(await screen.findByText("Mark"));
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(api.updateAgentSettings).toHaveBeenCalled());
    const sent = vi.mocked(api.updateAgentSettings).mock.calls[0]![1];
    expect(sent.voice_id).toBe("v-mark");
  });

  it("warns that a new voice republishes the receptionist", async () => {
    givenVoices();
    renderPage(<AgentGeneral />);

    expect(screen.queryByText(/publishes a new version/i)).not.toBeInTheDocument();
    fireEvent.click(await screen.findByText("Eric"));

    expect(screen.getByText(/publishes a new version/i)).toBeInTheDocument();
  });

  it("says when a voice has no sample rather than offering a dead button", async () => {
    givenVoices([
      voice({ voice_id: "v-quiet", label: "Quiet", preview_url: null, recommended: false }),
    ]);
    renderPage(<AgentGeneral />);

    fireEvent.click(await screen.findByRole("button", { name: /show 1 more/i }));

    expect(screen.getByText(/no sample for this one/i)).toBeInTheDocument();
    // One "Hear it" per voice that actually has one.
    expect(screen.getAllByRole("button", { name: /hear it/i })).toHaveLength(6);
  });

  it("sends the talking speed, and marks the one the vendor did not alter", async () => {
    givenVoices();
    renderPage(<AgentGeneral />);

    // The note belongs to the option, not to the selection: it says which speed
    // is the untouched recording, whether or not it is the one chosen.
    expect(await screen.findByText(/most lifelike/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "0.85" }));
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(api.updateAgentSettings).toHaveBeenCalled());
    expect(vi.mocked(api.updateAgentSettings).mock.calls[0]![1].speaking_speed).toBe(0.85);
  });

  it("plays a sample at the speed that is selected", async () => {
    // The speed control sits under the grid; a sample that ignored it would be
    // demonstrating a setting nobody chose.
    givenVoices();
    const play = vi
      .spyOn(window.HTMLMediaElement.prototype, "play")
      .mockResolvedValue(undefined);
    renderPage(<AgentGeneral />);

    fireEvent.click(await screen.findByRole("radio", { name: "1.2" }));
    const buttons = await screen.findAllByRole("button", { name: /hear it/i });
    fireEvent.click(buttons[0]!);

    expect(play).toHaveBeenCalled();
    const element = play.mock.instances[0] as HTMLAudioElement;
    expect(element.playbackRate).toBe(1.2);
    // Pitch left alone: the vendor's speed changes the speaking rate, it does
    // not resample, and a squeaky sample would be a different setting.
    expect(element.preservesPitch).toBe(true);
    play.mockRestore();
  });

  it("offers three responsiveness settings, because the vendor has three", async () => {
    // The reference design has four. ElevenLabs accepts patient, normal or
    // eager and nothing else, so a "Medium" beside "Auto" would be two names
    // for one behaviour.
    givenVoices();
    renderPage(<AgentGeneral />);

    expect(await screen.findByRole("radio", { name: "Auto" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Low" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "High" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Medium" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "Low" }));
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(api.updateAgentSettings).toHaveBeenCalled());
    expect(vi.mocked(api.updateAgentSettings).mock.calls[0]![1].responsiveness).toBe("low");
  });

  it("does not treat the vendor's defaults as an edit", async () => {
    // The form shows 1 and Auto for a receptionist that chose neither, and
    // opening a page must not look like changing it.
    givenVoices();
    renderPage(<AgentGeneral />);

    // The note is part of the option's accessible name on purpose: "most
    // lifelike" is information, not decoration.
    expect(await screen.findByRole("radio", { name: "1 Most lifelike" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Auto" })).toBeChecked();
    expect(screen.getByRole("button", { name: /save changes/i })).toBeDisabled();
  });

  it("shows a member the voices but cannot change them", async () => {
    givenVoices();
    renderPage(<AgentGeneral />, MEMBER);

    expect(await screen.findByText("Sarah")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Sarah/ })).toHaveAttribute("aria-disabled", "true");
  });
});

// ---------------------------------------------------------------------------
// Receptionists — the gallery
// ---------------------------------------------------------------------------

function receptionist(overrides: Partial<ReceptionistCardView> = {}): ReceptionistCardView {
  return {
    tenant_id: "t1",
    name: "Careful Legal",
    status: "active",
    role: "owner",
    agent_name: null,
    avatar: "ada",
    voice: "Sarah",
    phone_e164: "+18055550143",
    live: true,
    calls_recent: 12,
    activity_days: 30,
    created_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

describe("Receptionists — the gallery", () => {
  beforeEach(() => {
    navigation.pathname = "/dashboard/agents";
  });

  it("shows each receptionist with its number, voice and recent calls", async () => {
    vi.mocked(api.listReceptionists).mockResolvedValue([receptionist()]);
    renderPage(<ReceptionistsView />);

    expect(await screen.findByRole("heading", { name: "Careful Legal" })).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByText(/\(805\) 555-0143/)).toBeInTheDocument();
    expect(screen.getByText("Sarah")).toBeInTheDocument();
    expect(screen.getByText(/calls in the last 30 days/)).toBeInTheDocument();
  });

  it("says a business is not answering even while it is active", async () => {
    // Paid up and provisioned is not the same as something picking up, and the
    // badge has to report what a caller would actually get.
    vi.mocked(api.listReceptionists).mockResolvedValue([
      receptionist({ live: false, phone_e164: null }),
    ]);
    renderPage(<ReceptionistsView />);

    expect(await screen.findByText("Not answering")).toBeInTheDocument();
    expect(screen.getByText("No number yet")).toBeInTheDocument();
    // Nothing to call, so it offers the thing that would fix it instead.
    expect(screen.queryByRole("link", { name: /call it/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /finish setup/i })).toBeInTheDocument();
  });

  it("keeps a deleted business visible but offers nothing that acts on it", async () => {
    vi.mocked(api.listReceptionists).mockResolvedValue([receptionist({ status: "cancelled" })]);
    renderPage(<ReceptionistsView />);

    expect(await screen.findByText("Deleted")).toBeInTheDocument();
    expect(screen.getByText(/calls are kept on the account/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^edit$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /actions for/i })).not.toBeInTheDocument();
  });

  it("switches the workspace before opening one, not after", async () => {
    // Navigating without switching would open this card's Agent page showing
    // the previous business's receptionist.
    vi.mocked(api.listReceptionists).mockResolvedValue([
      receptionist({ tenant_id: "t2", name: "Sunset Salon" }),
    ]);
    renderPage(<ReceptionistsView />);

    fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));

    expect(navigation.push).toHaveBeenCalledWith("/dashboard/agent");
    // Every page under here reads one tenant from the workspace, so the proof
    // is that the workspace went and fetched the card's tenant.
    await waitFor(() =>
      expect(vi.mocked(api.getTenant).mock.calls.some((call) => call[0] === "t2")).toBe(true),
    );
  });

  it("calls a failed setup what it is", async () => {
    // `failed` is a real tenant state and used to fall through to "Setting up",
    // which would have told an owner to wait for something that had stopped.
    vi.mocked(api.listReceptionists).mockResolvedValue([
      receptionist({ status: "failed", live: false, phone_e164: null }),
    ]);
    renderPage(<ReceptionistsView />);

    expect(await screen.findByText("Setup failed")).toBeInTheDocument();
  });

  it("offers a way in when there is nothing yet", async () => {
    vi.mocked(api.listReceptionists).mockResolvedValue([]);
    renderPage(<ReceptionistsView />);

    expect(await screen.findByText(/no receptionists yet/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /set up a receptionist/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Agent / Tags
// ---------------------------------------------------------------------------

function callTag(overrides: Partial<CallTagView> = {}): CallTagView {
  return {
    id: "tag-1",
    name: "Pricing",
    description: "Caller asks about pricing, invoices or refunds.",
    color: "amber",
    enabled: true,
    position: 1,
    created_at: "2026-10-20T00:00:00Z",
    ...overrides,
  };
}

function tagsView(tags: CallTagView[], max = 25): CallTagsView {
  return { tags, max_tags: max, active: tags.filter((tag) => tag.enabled).length };
}

describe("Agent / Tags", () => {
  beforeEach(() => {
    navigation.pathname = "/dashboard/agent";
    navigation.search = "?section=tags";
  });

  it("asks for a tag when there are none", async () => {
    vi.mocked(api.listCallTags).mockResolvedValue(tagsView([]));
    renderPage(<AgentTags />);

    expect(await screen.findByText(/sort your calls by what they were about/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add tag/i })).toBeEnabled();
  });

  it("sends the name, the rule and the colour", async () => {
    vi.mocked(api.listCallTags).mockResolvedValue(tagsView([]));
    vi.mocked(api.createCallTag).mockResolvedValue(tagsView([callTag()]));
    renderPage(<AgentTags />);

    fireEvent.click(await screen.findByRole("button", { name: /add tag/i }));
    fireEvent.change(screen.getByLabelText("Tag name"), { target: { value: "Pricing" } });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Caller asks about pricing." },
    });
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "amber" } });
    fireEvent.click(screen.getByRole("button", { name: /save tag/i }));

    await waitFor(() =>
      expect(api.createCallTag).toHaveBeenCalledWith("t1", {
        name: "Pricing",
        description: "Caller asks about pricing.",
        color: "amber",
      }),
    );
  });

  it("will not send a tag with no rule for applying it", async () => {
    // An empty description is handed to the classifier with no rule, and every
    // call becomes a candidate for the tag.
    vi.mocked(api.listCallTags).mockResolvedValue(tagsView([]));
    renderPage(<AgentTags />);

    fireEvent.click(await screen.findByRole("button", { name: /add tag/i }));
    fireEvent.change(screen.getByLabelText("Tag name"), { target: { value: "Pricing" } });
    fireEvent.click(screen.getByRole("button", { name: /save tag/i }));

    expect(await screen.findByText(/say when your receptionist should apply/i)).toBeInTheDocument();
    expect(api.createCallTag).not.toHaveBeenCalled();
  });

  it("counts active tags against the limit and says what frees a slot", async () => {
    const full = Array.from({ length: 25 }, (_, index) =>
      callTag({ id: `tag-${index}`, name: `Tag ${index}` }),
    );
    vi.mocked(api.listCallTags).mockResolvedValue(tagsView(full));
    renderPage(<AgentTags />);

    expect(await screen.findByText(/25 of 25 active/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add tag/i })).toBeDisabled();
    // Switching one off is the way out, and it keeps the history.
    expect(screen.getByText(/switch one off to free a slot/i)).toBeInTheDocument();
  });

  it("switches a tag off without deleting it", async () => {
    vi.mocked(api.listCallTags).mockResolvedValue(tagsView([callTag()]));
    vi.mocked(api.updateCallTag).mockResolvedValue(tagsView([callTag({ enabled: false })]));
    renderPage(<AgentTags />);

    fireEvent.click(await screen.findByRole("checkbox", { name: /switch off pricing/i }));

    await waitFor(() =>
      expect(api.updateCallTag).toHaveBeenCalledWith("t1", "tag-1", { enabled: false }),
    );
  });

  it("warns that deleting a tag takes it off past calls", async () => {
    // The difference between the two controls is the thing somebody discovers
    // at the worst moment, so the confirmation says it.
    vi.mocked(api.listCallTags).mockResolvedValue(tagsView([callTag()]));
    vi.mocked(api.deleteCallTag).mockResolvedValue(tagsView([]));
    renderPage(<AgentTags />);

    fireEvent.click(await screen.findByRole("button", { name: /actions for pricing/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete tag/i }));

    expect(screen.getByText(/removes the tag from every call it was applied to/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^delete tag$/i }));
    await waitFor(() => expect(api.deleteCallTag).toHaveBeenCalledWith("t1", "tag-1"));
  });

  it("shows a member the tags but none of the controls", async () => {
    vi.mocked(api.listCallTags).mockResolvedValue(tagsView([callTag()]));
    renderPage(<AgentTags />, MEMBER);

    expect(await screen.findByText("Pricing")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add tag/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /actions for/i })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /switch off pricing/i })).toBeDisabled();
  });
});
