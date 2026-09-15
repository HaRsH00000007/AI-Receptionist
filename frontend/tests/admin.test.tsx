/**
 * The operator console.
 *
 * This is the one page in the product that can spend and un-spend money, so the
 * tests concentrate on the guards around that: a destructive action names its
 * consequence before it happens, a refused key does not linger, and the
 * credential never reaches storage that outlives the tab.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import AdminView from "@/app/admin/AdminView";
import AdminPage from "@/app/admin/page";
import { ApiError } from "@/lib/api";
import type { AgentConfigView, ReadinessView, RunSummaryView } from "@/lib/types";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listRuns: vi.fn(),
    retryRun: vi.fn(),
    abandonRun: vi.fn(),
    listConfigs: vi.fn(),
    getConfigDetail: vi.fn(),
    rollbackConfig: vi.fn(),
    resyncAgent: vi.fn(),
    getReadiness: vi.fn(),
  };
});

const api = await import("@/lib/api");

const FAILED_RUN: RunSummaryView = {
  run_id: "r1",
  tenant_id: "t1",
  business_name: "Sunset Salon",
  status: "failed",
  current_step: "purchase_number",
  attempt: 3,
  last_error: "twilio: no numbers available in 805",
  started_at: "2026-09-11T10:00:00Z",
  finished_at: null,
};

const READY: ReadinessView = {
  status: "ready",
  checks: { database: { ok: true, detail: null, critical: true } },
  degraded: [],
};

const CONFIGS: AgentConfigView[] = [
  {
    version: 2,
    is_live: true,
    generated_by: "llm",
    generator_detail: null,
    template_version: "salon.v3",
    voice_id: "voice_1",
    created_at: "2026-09-12T10:00:00Z",
  },
  {
    version: 1,
    is_live: false,
    generated_by: "template_fallback",
    generator_detail: null,
    template_version: "salon.v2",
    voice_id: "voice_1",
    created_at: "2026-09-11T10:00:00Z",
  },
];

function run(overrides: Partial<RunSummaryView>): RunSummaryView {
  return { ...FAILED_RUN, ...overrides };
}

function renderConsole() {
  return render(<AdminView adminKey="k" onUnauthorized={() => {}} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listRuns).mockResolvedValue([FAILED_RUN]);
  vi.mocked(api.getReadiness).mockResolvedValue(READY);
  vi.mocked(api.listConfigs).mockResolvedValue(CONFIGS);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the operator console", () => {
  it("shows a failed run with its error and the step it failed on", async () => {
    renderConsole();

    expect(await screen.findByText("Sunset Salon")).toBeInTheDocument();
    expect(screen.getByText(/no numbers available/)).toBeInTheDocument();
    // The step is shown in the operator's language, not the enum's.
    expect(screen.getByText("Provisioning phone number")).toBeInTheDocument();
  });

  it("retries a run without asking for confirmation", async () => {
    // Retry is idempotent: every step is guarded by an adoption check, so a
    // double-click cannot buy a second number. Prompting would train operators
    // to click through prompts that do matter.
    vi.mocked(api.retryRun).mockResolvedValue({ ok: true, detail: "run re-armed" });
    renderConsole();
    await screen.findByText("Sunset Salon");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(vi.mocked(api.retryRun)).toHaveBeenCalledWith("k", "r1"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("names what abandoning will do before doing it", async () => {
    vi.mocked(api.abandonRun).mockResolvedValue({ ok: true, detail: "compensated" });
    renderConsole();
    await screen.findByText("Sunset Salon");

    fireEvent.click(screen.getByRole("button", { name: "Abandon" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Sunset Salon");
    expect(dialog).toHaveTextContent(/releases any phone number/i);
    expect(dialog).toHaveTextContent(/will not have a working receptionist/i);
    expect(vi.mocked(api.abandonRun)).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Abandon run" }));

    await waitFor(() => expect(vi.mocked(api.abandonRun)).toHaveBeenCalledWith("k", "r1"));
  });

  it("does nothing when the operator cancels", async () => {
    renderConsole();
    await screen.findByText("Sunset Salon");

    fireEvent.click(screen.getByRole("button", { name: "Abandon" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(vi.mocked(api.abandonRun)).not.toHaveBeenCalled();
  });

  it("reports a refused key upward rather than showing an empty list", async () => {
    // An empty table would read as "nothing is failing", which is the most
    // dangerous thing this page could say while actually being unauthorized.
    const onUnauthorized = vi.fn();
    vi.mocked(api.listRuns).mockRejectedValue(
      new ApiError("invalid or missing admin api key", { status: 403 }),
    );

    render(<AdminView adminKey="wrong" onUnauthorized={onUnauthorized} />);

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalled());
  });

  it("surfaces a failed action instead of silently doing nothing", async () => {
    vi.mocked(api.retryRun).mockRejectedValue(
      new ApiError("run is already terminal", { status: 409 }),
    );

    renderConsole();
    await screen.findByText("Sunset Salon");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already terminal/i);
  });

  it("filters runs by status", async () => {
    renderConsole();
    await screen.findByText("Sunset Salon");

    fireEvent.change(screen.getByLabelText("Filter by status"), {
      target: { value: "billing_blocked" },
    });

    await waitFor(() =>
      expect(vi.mocked(api.listRuns)).toHaveBeenCalledWith("k", {
        status: "billing_blocked",
        limit: 100,
      }),
    );
  });

  it("says so plainly when a filter matches nothing", async () => {
    vi.mocked(api.listRuns).mockResolvedValue([]);
    renderConsole();

    expect(await screen.findByText(/nothing matches that filter/i)).toBeInTheDocument();
  });
});

describe("the overview", () => {
  it("counts each tenant once, by its newest run", async () => {
    // Newest first, as the API returns them. Tenant t1 failed after an earlier
    // active run, so it is a failure, not an active receptionist.
    const recent: RunSummaryView[] = [
      run({ run_id: "r5", tenant_id: "t2", business_name: "Bright Dental", status: "active" }),
      run({ run_id: "r4", tenant_id: "t1", status: "failed" }),
      run({ run_id: "r3", tenant_id: "t1", status: "active" }),
      run({ run_id: "r2", tenant_id: "t3", business_name: "Oak Law", status: "billing_blocked" }),
      run({ run_id: "r1", tenant_id: "t4", business_name: "Pine Homes", status: "validated" }),
    ];
    vi.mocked(api.listRuns).mockImplementation(async (_key, options) =>
      options?.limit === 200 ? recent : [FAILED_RUN],
    );

    renderConsole();

    const value = async (label: string) =>
      within(await screen.findByRole("group", { name: label })).getByText(/^\d+$/).textContent;

    expect(await value("Tenants")).toBe("4");
    expect(await value("Active receptionists")).toBe("1");
    expect(await value("In progress")).toBe("1");
    expect(await value("Waiting on billing")).toBe("1");
    expect(await value("Provisioning failures")).toBe("1");
  });

  it("shows a dependency that is not ready", async () => {
    vi.mocked(api.getReadiness).mockResolvedValue({
      status: "not_ready",
      checks: { database: { ok: false, detail: "connection refused", critical: true } },
      degraded: [],
    });

    renderConsole();

    expect(await screen.findByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText("connection refused")).toBeInTheDocument();
  });
});

describe("the tenant drawer", () => {
  async function openDrawer() {
    renderConsole();
    await screen.findByText("Sunset Salon");
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    return screen.findByRole("dialog", { name: "Sunset Salon" });
  }

  it("lists the tenant's configuration versions", async () => {
    const drawer = await openDrawer();

    expect(await within(drawer).findByText("v2")).toBeInTheDocument();
    expect(within(drawer).getByText("v1")).toBeInTheDocument();
    expect(vi.mocked(api.listConfigs)).toHaveBeenCalledWith("k", "t1");
  });

  it("asks before making an older version live", async () => {
    vi.mocked(api.rollbackConfig).mockResolvedValue({
      ok: true,
      detail: "config v1 is live; run resync-agent to push it to the vendor",
    });
    const drawer = await openDrawer();
    await within(drawer).findByText("v1");

    fireEvent.click(within(drawer).getByRole("button", { name: "Make live" }));

    const confirm = await screen.findByRole("dialog", { name: "Make version 1 live?" });
    expect(confirm).toHaveTextContent(/resync agent/i);
    expect(vi.mocked(api.rollbackConfig)).not.toHaveBeenCalled();

    fireEvent.click(within(confirm).getByRole("button", { name: "Make v1 live" }));

    await waitFor(() => expect(vi.mocked(api.rollbackConfig)).toHaveBeenCalledWith("k", "t1", 1));
  });
});

describe("the admin key", () => {
  it("is not persisted anywhere that outlives the tab", async () => {
    // An XSS bug on a page that stored this credential would hand an attacker
    // the ability to release customers' phone numbers.
    const setItem = vi.fn();
    vi.stubGlobal("localStorage", {
      setItem,
      getItem: vi.fn().mockReturnValue(null),
      removeItem: vi.fn(),
    });

    render(<AdminPage />);

    fireEvent.change(screen.getByLabelText("Admin key"), {
      target: { value: "super-secret-operator-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(vi.mocked(api.listRuns)).toHaveBeenCalled());
    expect(setItem).not.toHaveBeenCalled();
    expect(document.cookie).not.toContain("super-secret-operator-key");
  });

  it("is entered in a masked field", () => {
    render(<AdminPage />);
    expect(screen.getByLabelText("Admin key")).toHaveAttribute("type", "password");
  });

  it("returns to the prompt when the key is refused", async () => {
    vi.mocked(api.listRuns).mockRejectedValue(
      new ApiError("invalid or missing admin api key", { status: 403 }),
    );

    render(<AdminPage />);
    fireEvent.change(screen.getByLabelText("Admin key"), {
      target: { value: "wrong" },
    });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/refused/i);
  });

  it("is dropped when the console is locked", async () => {
    render(<AdminPage />);
    fireEvent.change(screen.getByLabelText("Admin key"), { target: { value: "k" } });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    fireEvent.click(await screen.findByRole("button", { name: "Lock console" }));

    expect(screen.getByLabelText("Admin key")).toHaveValue("");
  });
});
