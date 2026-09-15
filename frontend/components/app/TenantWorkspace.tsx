"use client";

/**
 * Everything the signed-in dashboard knows about one tenant, loaded once and
 * shared by every dashboard page.
 *
 * **The tenant id is never taken from the client.** It comes from the session's
 * memberships, and every request is re-authorized server-side against the
 * session cookie. A dashboard that accepted a tenant id from the URL and
 * trusted it would be an IDOR: change the id, read another business's calls.
 * The id still appears in request paths — it has to — but it is a *request*,
 * and the server decides.
 *
 * Everything here is read-only. Nothing in the dashboard can spend money,
 * change a prompt or release a number.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  getAgentOrNull,
  getPhoneOrNull,
  getProfileOrNull,
  getProvisioning,
  getTenant,
  getUsage,
  listCalls,
  logout,
} from "@/lib/api";
import {
  isTerminal,
  type AgentView,
  type BusinessProfileView,
  type CallView,
  type PhoneNumberView,
  type ProvisioningView,
  type SessionView,
  type TenantView,
  type UsageView,
} from "@/lib/types";

export interface TenantData {
  tenant: TenantView;
  provisioning: ProvisioningView;
  phone: PhoneNumberView | null;
  agent: AgentView | null;
  usage: UsageView;
  calls: CallView[];
  profile: BusinessProfileView | null;
}

interface WorkspaceValue {
  session: SessionView;
  tenantId: string;
  switchTenant: (tenantId: string) => void;
  data: TenantData | null;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  refresh: () => void;
  signOut: () => Promise<void>;
}

/** Poll while provisioning is still moving; stop once it settles. */
const POLL_INTERVAL_MS = 5_000;

/** The API's ceiling. The calls page filters within this window. */
export const CALL_FETCH_LIMIT = 100;

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

function initialTenant(session: SessionView): string {
  const memberships = session.memberships;
  const active = memberships.find((membership) => membership.tenant_id === session.active_tenant_id);
  return active?.tenant_id ?? memberships[0]?.tenant_id ?? "";
}

export function TenantWorkspace({
  session,
  onSignedOut,
  children,
}: {
  session: SessionView;
  onSignedOut: () => void;
  children: ReactNode;
}) {
  const toast = useToast();
  const [tenantId, setTenantId] = useState(() => initialTenant(session));
  const [data, setData] = useState<TenantData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  //: Bumped to re-run the effect, so the fetch logic lives in exactly one place.
  const [reloadNonce, setReloadNonce] = useState(0);

  const settled =
    data !== null && data.tenant.id === tenantId && isTerminal(data.provisioning.status);

  // One effect owns the whole fetch-and-poll lifecycle. The fetcher is defined
  // inside it, which keeps every state write behind an `await`.
  //
  // `settled` in the dependencies means the effect re-runs once as
  // provisioning finishes, costing one extra fetch and buying an unconditional
  // stop: a dashboard left open overnight does not keep polling.
  useEffect(() => {
    let cancelled = false;

    async function load(): Promise<void> {
      if (!tenantId) return;
      try {
        // Fetched together so a page never renders a half-state where the
        // number is present but the status still says "setting up".
        const [tenant, provisioning, phone, agent, usage, calls, profile] = await Promise.all([
          getTenant(tenantId),
          getProvisioning(tenantId),
          getPhoneOrNull(tenantId),
          getAgentOrNull(tenantId),
          getUsage(tenantId),
          listCalls(tenantId, undefined, { limit: CALL_FETCH_LIMIT }),
          getProfileOrNull(tenantId),
        ]);
        if (cancelled) return;
        setData({ tenant, provisioning, phone, agent, usage, calls, profile });
        setError(null);
      } catch (caught) {
        if (cancelled) return;
        if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) {
          onSignedOut();
          return;
        }
        // A 404 here means the server declined to confirm the tenant exists —
        // the deliberate anti-enumeration answer, not a missing page.
        setError(caught instanceof ApiError ? caught.message : "Could not load your dashboard.");
      } finally {
        if (!cancelled) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    }

    void load();
    if (settled) {
      return () => {
        cancelled = true;
      };
    }
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [tenantId, settled, reloadNonce, onSignedOut]);

  const refresh = useCallback(() => {
    setRefreshing(true);
    setReloadNonce((value) => value + 1);
  }, []);

  const switchTenant = useCallback((next: string) => {
    setLoading(true);
    setData(null);
    setError(null);
    setTenantId(next);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await logout();
    } catch {
      // The session is still valid server-side, so pretending to have signed
      // out would leave a working cookie behind on a shared computer.
      toast({
        tone: "danger",
        title: "Couldn't sign you out",
        description: "Check your connection and try again.",
      });
      return;
    }
    onSignedOut();
  }, [onSignedOut, toast]);

  const value = useMemo<WorkspaceValue>(
    () => ({
      session,
      tenantId,
      switchTenant,
      data,
      loading,
      refreshing,
      error,
      refresh,
      signOut,
    }),
    [session, tenantId, switchTenant, data, loading, refreshing, error, refresh, signOut],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside <TenantWorkspace>");
  return value;
}

/**
 * For pages rendered inside the shell, which only renders them once data has
 * loaded — so a page never has to handle the "nothing yet" case itself.
 */
export function useTenantData(): TenantData & Omit<WorkspaceValue, "data"> {
  const { data, ...rest } = useWorkspace();
  if (!data) throw new Error("useTenantData called before the workspace loaded");
  return { ...data, ...rest };
}
