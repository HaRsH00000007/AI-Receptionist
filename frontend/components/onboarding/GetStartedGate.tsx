"use client";

/**
 * The business-setup form, for the right person at the right time.
 *
 * Account first, so:
 *
 * - not signed in                -> create an account (`/signup`)
 * - signed in, no business yet   -> the form, reopened where it was left
 * - signed in, already a member  -> the dashboard, which routes by state
 *
 * Every "Get started" button on the site links here, including the ones a
 * signed-in customer sees; without this a live customer would be shown the
 * form again. `?another=1` is the deliberate way through, for an owner adding
 * a second business.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { getOnboardingDraft, getSessionOrNull, logout } from "@/lib/api";
import { onboardingRedirect } from "@/lib/routing";
import type { SessionView } from "@/lib/types";

import { SignupWizard, restoreDraft, type WizardDraft } from "./SignupWizard";

type Check =
  | { status: "checking" }
  | { status: "redirecting"; to: string }
  | { status: "show"; session: SessionView; draft: WizardDraft | null }
  | { status: "unavailable" };

export function GetStartedGate() {
  const router = useRouter();
  const another = useSearchParams()?.get("another") === "1";
  const [check, setCheck] = useState<Check>({ status: "checking" });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let session: SessionView | null;
      try {
        session = await getSessionOrNull();
      } catch {
        if (!cancelled) setCheck({ status: "unavailable" });
        return;
      }
      const target = onboardingRedirect(session, another);
      if (cancelled) return;
      if (target || !session) {
        setCheck({ status: "redirecting", to: target ?? "/signup" });
        router.replace(target ?? "/signup");
        return;
      }
      // A saved draft is a convenience: if it cannot be read, start fresh
      // rather than blocking the form.
      let draft: WizardDraft | null = null;
      try {
        const saved = await getOnboardingDraft();
        if (saved.data) draft = restoreDraft(saved.data, session.email);
      } catch {
        draft = null;
      }
      if (!cancelled) setCheck({ status: "show", session, draft });
    })();
    return () => {
      cancelled = true;
    };
  }, [another, router]);

  if (check.status === "show") {
    return (
      <div className="space-y-6">
        <AccountBar email={check.session.email} onSignedOut={() => router.replace("/login")} />
        <SignupWizard accountEmail={check.session.email} draft={check.draft} />
      </div>
    );
  }

  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-3">
      {check.status !== "unavailable" && <Spinner className="size-5 text-accent" />}
      <p role="status" className="text-sm text-muted">
        {check.status === "unavailable"
          ? "We couldn't reach the server. Check your connection and reload the page."
          : check.status === "redirecting"
            ? check.to === "/signup"
              ? "Taking you to create your account…"
              : "Taking you to your dashboard…"
            : "Loading…"}
      </p>
    </div>
  );
}

function AccountBar({ email, onSignedOut }: { email: string; onSignedOut: () => void }) {
  const [signingOut, setSigningOut] = useState(false);
  return (
    <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-sm">
      <p className="text-muted">
        Signed in as <span className="font-semibold text-ink">{email}</span>. Your progress is saved
        to your account.
      </p>
      <Button
        size="sm"
        variant="ghost"
        loading={signingOut}
        onClick={async () => {
          setSigningOut(true);
          try {
            await logout();
            onSignedOut();
          } finally {
            setSigningOut(false);
          }
        }}
      >
        Sign out
      </Button>
    </div>
  );
}
