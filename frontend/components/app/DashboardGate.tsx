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
import { Button, ButtonLink } from "@/components/ui/Button";
import { LogoMark } from "@/components/ui/Logo";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError, getSessionOrNull, logout } from "@/lib/api";
import type { SessionView } from "@/lib/types";

import { AppShell } from "./AppShell";
import { TenantWorkspace } from "./TenantWorkspace";

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

  useEffect(() => {
    if (state.status === "signed_out") router.replace("/login");
  }, [state.status, router]);

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
    return (
      <CenteredPanel>
        <div className="card w-full max-w-md p-8 text-left">
          <h1 className="text-xl font-bold tracking-tight">No organizations yet</h1>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            You&apos;re signed in as <span className="font-medium text-ink">{session.email}</span>,
            but this account isn&apos;t a member of any business. If you were invited, accept the
            invitation from the email you received.
          </p>
          <div className="mt-6 flex flex-wrap gap-2.5">
            <ButtonLink href="/get-started">Set up a receptionist</ButtonLink>
            <Button
              variant="secondary"
              onClick={async () => {
                try {
                  await logout();
                } finally {
                  onSignedOut();
                }
              }}
            >
              Sign out
            </Button>
          </div>
        </div>
      </CenteredPanel>
    );
  }

  return (
    <TenantWorkspace session={session} onSignedOut={onSignedOut}>
      <AppShell>{children}</AppShell>
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
