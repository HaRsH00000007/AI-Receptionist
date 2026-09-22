"use client";

/**
 * Resolves the session before rendering anything tenant-shaped.
 *
 * A signed-out visitor is sent to the login page rather than shown an empty
 * dashboard: an empty dashboard invites a reload loop and, worse, looks like
 * an account with no data rather than a session that expired.
 *
 * The gate is a convenience, not the security boundary. Every request the
 * dashboard makes is authorized server-side against the session cookie, so a
 * user who removed this check client-side would still be refused by the API.
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { LogoMark } from "@/components/ui/Logo";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError, getSessionOrNull } from "@/lib/api";
import { ONBOARDING_PATH } from "@/lib/routing";
import type { SessionView } from "@/lib/types";

import { TenantWorkspace } from "./TenantWorkspace";
import { WorkspaceFrame } from "./WorkspaceFrame";

type GateState =
  | { status: "checking" }
  | { status: "signed_in"; session: SessionView }
  | { status: "signed_out" }
  | { status: "error"; message: string };

export function DashboardGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<GateState>({ status: "checking" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const session = await getSessionOrNull();
        if (cancelled) return;
        setState(session ? { status: "signed_in", session } : { status: "signed_out" });
      } catch (caught) {
        if (cancelled) return;
        setState({
          status: "error",
          message: caught instanceof ApiError ? caught.message : "Could not check your session.",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // No business yet means onboarding is unfinished: resume the setup form,
  // which reopens the saved draft.
  const needsOnboarding = state.status === "signed_in" && state.session.memberships.length === 0;

  useEffect(() => {
    if (state.status === "signed_out") router.replace("/login");
    else if (needsOnboarding) router.replace(ONBOARDING_PATH);
  }, [state.status, needsOnboarding, router]);

  const onSignedOut = useCallback(() => setState({ status: "signed_out" }), []);

  if (state.status === "checking" || state.status === "signed_out") {
    return (
      <CenteredPanel>
        <Spinner className="size-5 text-accent" />
        <p role="status" className="mt-3 text-sm text-muted">
          {state.status === "checking" ? "Checking your session…" : "Taking you to sign in…"}
        </p>
      </CenteredPanel>
    );
  }

  if (state.status === "error") {
    return (
      <CenteredPanel>
        <Alert
          tone="danger"
          title="We couldn't check your session"
          action={
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setState({ status: "checking" });
                setAttempt((value) => value + 1);
              }}
            >
              Try again
            </Button>
          }
        >
          {state.message}
        </Alert>
      </CenteredPanel>
    );
  }

  const { session } = state;

  if (session.memberships.length === 0) {
    // The effect above is taking them to the setup form.
    return (
      <CenteredPanel>
        <Spinner className="size-5 text-accent" />
        <p role="status" className="mt-3 text-sm text-muted">
          Taking you to set up your business…
        </p>
      </CenteredPanel>
    );
  }

  return (
    <TenantWorkspace session={session} onSignedOut={onSignedOut}>
      <WorkspaceFrame>{children}</WorkspaceFrame>
    </TenantWorkspace>
  );
}

function CenteredPanel({ children }: { children: ReactNode }) {
  return (
    <main id="main" className="flex min-h-dvh flex-col items-center justify-center px-4 py-16">
      <LogoMark size={36} className="mb-6" />
      <div className="flex w-full max-w-md flex-col items-center">{children}</div>
    </main>
  );
}
