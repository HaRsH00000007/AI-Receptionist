"use client";

/**
 * "Already have an account? Sign in", for visitors who are not signed in.
 *
 * The onboarding layout serves both the business form (always signed in, now
 * that the account comes first) and the anonymous status page. Offering to
 * sign in to someone who already is would be noise, so the prompt hides itself
 * once a session is found.
 */

import { useEffect, useState } from "react";

import { ButtonLink } from "@/components/ui/Button";
import { getSessionOrNull } from "@/lib/api";

export function SignInPrompt() {
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSessionOrNull()
      .then((session) => {
        if (!cancelled) setSignedIn(session !== null);
      })
      .catch(() => {
        // Unknown: keep offering the sign-in link, which is harmless.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (signedIn) return null;
  return (
    <>
      <span className="hidden text-sm text-muted sm:inline">Already have an account?</span>
      <ButtonLink href="/login" variant="ghost" size="sm">
        Sign in
      </ButtonLink>
    </>
  );
}
