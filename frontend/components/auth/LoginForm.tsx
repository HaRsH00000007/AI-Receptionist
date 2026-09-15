"use client";

/**
 * Sign in.
 *
 * Passwordless: the form asks for an address and the backend emails a link.
 * Two properties of this form are security properties rather than UX choices:
 *
 * **The response never reveals whether an account exists.** The confirmation is
 * identical for a customer and for a stranger, because a form that said "no
 * such account" would be a free oracle for "is this business a customer of
 * yours?" — worth money to a competitor, and the first step of a targeted phish.
 *
 * **The token never reaches this component.** Exchanging a link happens on the
 * callback page and sets an HttpOnly cookie server-side.
 */

import Link from "next/link";
import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Field";
import { MailIcon } from "@/components/ui/icons";
import { ApiError, requestMagicLink } from "@/lib/api";
import { BRAND } from "@/lib/brand";

type Status = "idle" | "sending" | "sent" | "error";

export function LoginForm() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [resending, setResending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(resend = false) {
    if (resend) setResending(true);
    else setStatus("sending");
    setError(null);
    try {
      await requestMagicLink(email.trim());
      setStatus("sent");
    } catch (caught) {
      // Only a transport or rate-limit failure reaches here. An unknown address
      // is a success as far as this page is concerned, by design.
      setStatus("error");
      setError(caught instanceof ApiError ? caught.message : "Something went wrong. Please try again.");
    } finally {
      setResending(false);
    }
  }

  if (status === "sent") {
    return (
      <div className="animate-fade-up">
        <span className="flex size-12 items-center justify-center rounded-2xl bg-accent-soft text-accent">
          <MailIcon size={22} />
        </span>
        <h1 className="mt-6 text-[1.75rem] font-bold tracking-tight">Check your email</h1>
        <p role="status" className="mt-3 text-[0.9375rem] leading-relaxed text-muted">
          If <span className="font-semibold break-all text-ink">{email.trim()}</span> has an account,
          a sign-in link is on its way. The link works once and expires in 15 minutes.
        </p>
        <div className="mt-8 flex flex-col gap-2.5">
          <Button variant="secondary" size="lg" onClick={() => setStatus("idle")}>
            Use a different address
          </Button>
          <Button variant="ghost" loading={resending} onClick={() => void send(true)}>
            Send another link
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-[1.75rem] font-bold tracking-tight">Sign in</h1>
      <p className="mt-2 text-[0.9375rem] text-muted">
        We&apos;ll email you a secure sign-in link. No password to remember.
      </p>

      <form
        className="mt-8 space-y-5"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          if (email.trim()) void send();
        }}
      >
        <TextField
          label="Email address"
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@yourbusiness.com"
        />

        {error && (
          <Alert tone="danger" title="We couldn't send your link">
            {error}
          </Alert>
        )}

        <Button type="submit" block size="lg" loading={status === "sending"} disabled={!email.trim()}>
          {status === "sending" ? "Sending link…" : "Email me a sign-in link"}
        </Button>
      </form>

      <p className="mt-8 text-sm text-muted">
        New to {BRAND.name}?{" "}
        <Link href="/get-started" className="link">
          Set up your receptionist
        </Link>
      </p>
    </div>
  );
}
