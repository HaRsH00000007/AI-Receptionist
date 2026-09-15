"use client";

/**
 * Where the emailed sign-in link lands.
 *
 * The backend's email points at `/auth/callback?token=…`. This page trades the
 * token for a session (the API sets an HttpOnly cookie) and moves on to the
 * dashboard.
 *
 * Two details matter because the token is a single-use credential:
 *
 * - It is removed from the address bar before the exchange, so it does not
 *   linger in history, get bookmarked, or leak through a Referer header.
 * - It is exchanged at most once per page. React runs effects twice in
 *   development; a second exchange of a consumed token would fail and show the
 *   user an error for a sign-in that actually worked.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ButtonLink } from "@/components/ui/Button";
import { AlertTriangleIcon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError, exchangeMagicLink } from "@/lib/api";

export function CallbackWorking() {
  return (
    <div className="flex flex-col items-center text-center">
      <Spinner className="size-6 text-accent" />
      <h1 className="mt-5 text-xl font-bold tracking-tight">Signing you in…</h1>
      <p role="status" className="mt-2 text-sm text-muted">
        Checking your sign-in link.
      </p>
    </div>
  );
}

export function MagicLinkCallback() {
  const router = useRouter();
  const params = useSearchParams();
  const [initialToken] = useState(() => params.get("token"));
  const [failure, setFailure] = useState<string | null>(
    initialToken ? null : "This page needs the sign-in link from your email.",
  );
  const exchanged = useRef(false);

  useEffect(() => {
    if (!initialToken || exchanged.current) return;
    exchanged.current = true;
    window.history.replaceState(null, "", "/auth/callback");

    void (async () => {
      try {
        await exchangeMagicLink(initialToken);
        router.replace("/dashboard");
      } catch (caught) {
        setFailure(
          caught instanceof ApiError && caught.status !== 0
            ? "This sign-in link has expired or has already been used."
            : "We couldn't reach the server to finish signing you in.",
        );
      }
    })();
  }, [initialToken, router]);

  if (!failure) return <CallbackWorking />;

  return (
    <div className="animate-fade-up">
      <span className="flex size-12 items-center justify-center rounded-2xl bg-warn-soft text-warn">
        <AlertTriangleIcon size={22} />
      </span>
      <h1 className="mt-6 text-[1.75rem] font-bold tracking-tight">We couldn&apos;t sign you in</h1>
      <p role="alert" className="mt-3 text-[0.9375rem] leading-relaxed text-muted">
        {failure} Sign-in links work once and expire after a short time. Request a new one and use it
        straight away.
      </p>
      <ButtonLink href="/login" size="lg" block className="mt-8">
        Send me a new link
      </ButtonLink>
    </div>
  );
}
