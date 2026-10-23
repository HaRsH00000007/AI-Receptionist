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
import { getOnboardingDraft, getSessionOrNull, getWebsiteSetupOrNull, logout } from "@/lib/api";
import { onboardingRedirect } from "@/lib/routing";
import type { SessionView, WebsiteSetupView } from "@/lib/types";

import { SetupChoice, type SetupMode } from "./SetupChoice";
import { SignupWizard, restoreDraft, type WizardDraft } from "./SignupWizard";
import { WebsiteWizard } from "./WebsiteWizard";

type Check =
  | { status: "checking" }
  | { status: "redirecting"; to: string }
  | { status: "show"; session: SessionView; draft: WizardDraft | null; website: WebsiteSetupView | null }
  | { status: "unavailable" };

export function GetStartedGate() {
  const router = useRouter();
  const params = useSearchParams();
  const another = params?.get("another") === "1";
  const requested = params?.get("mode");
  const mode: SetupMode | null =
    requested === "website" ? "website" : requested === "manual" ? "manual" : null;
  /**
   * "Show me both options", said deliberately — by Back on the first step of a
   * flow, which is the only way to say it.
   *
   * It needs to be its own value rather than the absence of `mode`, because an
   * unfinished website setup resumes on a bare `/get-started` and "no mode" is
   * what landing there looks like. Without this, backing out would strip the
   * parameter, the draft would put the flow straight back on screen, and Back
   * would look broken.
   */
  const choosing = requested === "choose";
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
      // An unfinished website setup is resumed, whatever mode the URL asks for:
      // a half-read website is the strongest signal of where someone was.
      let website: WebsiteSetupView | null = null;
      try {
        website = await getWebsiteSetupOrNull();
      } catch {
        website = null;
      }
      if (!cancelled) setCheck({ status: "show", session, draft, website });
    })();
    return () => {
      cancelled = true;
    };
  }, [another, router]);

  if (check.status === "show") {
    const resumable = check.website && check.website.status !== "confirmed";
    /**
     * An unfinished website setup decides where someone *lands*; a choice in
     * the URL outranks it.
     *
     * The draft used to win unconditionally, which quietly disabled both ways
     * out of the website flow for the only people who could be in it: Back on
     * the first step, and "fill in a form instead". Each rewrote the URL, the
     * draft overrode it, and the same screen came straight back.
     */
    const chosen: SetupMode | null = choosing ? null : (mode ?? (resumable ? "website" : null));

    function leave() {
      // Back out of a flow: ask for the choice screen explicitly and show both
      // options again. The draft stays on the server, so choosing the website
      // flow again resumes where they were.
      const query = new URLSearchParams(params?.toString() ?? "");
      query.set("mode", "choose");
      router.replace(`/get-started?${query.toString()}`);
    }

    function choose(next: SetupMode) {
      // In the URL, not in state: a refresh mid-setup should not dump someone
      // back on the choice screen.
      const query = new URLSearchParams(params?.toString() ?? "");
      query.set("mode", next);
      router.replace(`/get-started?${query.toString()}`);
    }

    return (
      <div className="space-y-6">
        <AccountBar email={check.session.email} onSignedOut={() => router.replace("/login")} />
        {chosen === null && <SetupChoice onChoose={choose} />}
        {chosen === "website" && (
          <WebsiteWizard
            accountEmail={check.session.email}
            accountName={check.session.full_name ?? ""}
            existing={check.website}
            onSwitchToManual={() => choose("manual")}
            onLeave={leave}
          />
        )}
        {chosen === "manual" && (
          <SignupWizard accountEmail={check.session.email} draft={check.draft} />
        )}
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
