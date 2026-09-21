"use client";

/**
 * Sign in.
 *
 * Two ways in, and the form leads with the password because it is the one that
 * works on the day a business signs up — a link cannot be sent before outbound
 * email is configured. The link stays one click away for anyone who has
 * forgotten the password, or whose account never set one.
 *
 * Three properties here are security properties rather than UX choices:
 *
 * **Neither path reveals whether an account exists.** The password error is the
 * same sentence for a wrong password and an unknown address, and the link
 * confirmation is identical for a customer and a stranger. A form that said "no
 * such account" would be a free oracle for "is this business a customer of
 * yours?" — worth money to a competitor, and the first step of a targeted phish.
 *
 * **The session token never reaches this component.** Both paths set an
 * HttpOnly cookie server-side; the browser holds a credential JavaScript cannot
 * read, so an XSS bug cannot exfiltrate it.
 *
 * **The password lives in component state and nowhere else.** It is never put
 * in a URL, a query string or storage, and the field is cleared on failure.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Field";
import { MailIcon } from "@/components/ui/icons";
import { ApiError, requestMagicLink, signInWithPassword } from "@/lib/api";
import { BRAND } from "@/lib/brand";

type Mode = "password" | "link";
type Status = "idle" | "working" | "sent" | "error";

/**
 * Shown for every rejected sign-in, whatever was actually wrong.
 *
 * The backend already answers identically for a wrong password, an unknown
 * address, an account that signs in by link only and a suspended account. This
 * string is the other half of that: rendering the server's message verbatim
 * would be fine today and would leak the distinction the first time someone
 * made those messages more helpful.
 */
const REFUSED = "That email and password don't match an account.";

export function LoginForm() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [resending, setResending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function switchTo(next: Mode) {
    setMode(next);
    setStatus("idle");
    setError(null);
    setPassword("");
  }

  async function signIn() {
    setStatus("working");
    setError(null);
    try {
      await signInWithPassword(email.trim(), password);
      // A full navigation rather than a client push: the dashboard is rendered
      // against the new cookie, and a soft transition could serve a tree that
      // was built while signed out.
      router.replace("/dashboard");
      router.refresh();
    } catch (caught) {
      setStatus("error");
      setPassword("");
      const rateLimited = caught instanceof ApiError && caught.status === 429;
      setError(
        rateLimited
          ? caught.message
          : caught instanceof ApiError && caught.status === 401
            ? REFUSED
            : "Something went wrong. Please try again.",
      );
    }
  }

  async function sendLink(resend = false) {
    if (resend) setResending(true);
    else setStatus("working");
    setError(null);
    try {
      await requestMagicLink(email.trim());
      setStatus("sent");
    } catch (caught) {
      // Only a transport or rate-limit failure reaches here. An unknown address
      // is a success as far as this page is concerned, by design.
      setStatus("error");
      setError(
        caught instanceof ApiError ? caught.message : "Something went wrong. Please try again.",
      );
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
          <Button variant="secondary" size="lg" onClick={() => switchTo("password")}>
            Back to sign in
          </Button>
          <Button variant="ghost" loading={resending} onClick={() => void sendLink(true)}>
            Send another link
          </Button>
        </div>
      </div>
    );
  }

  const linkMode = mode === "link";
  const ready = linkMode ? Boolean(email.trim()) : Boolean(email.trim() && password);

  return (
    <div>
      <h1 className="text-[1.75rem] font-bold tracking-tight">Sign in</h1>
      <p className="mt-2 text-[0.9375rem] text-muted">
        {linkMode
          ? "We'll email you a secure sign-in link. No password needed."
          : `Welcome back to ${BRAND.name}.`}
      </p>

      <form
        className="mt-8 space-y-5"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          if (!ready) return;
          void (linkMode ? sendLink() : signIn());
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

        {!linkMode && (
          <TextField
            label="Password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Your password"
          />
        )}

        {error && (
          <Alert tone="danger" title={linkMode ? "We couldn't send your link" : "Can't sign in"}>
            {error}
          </Alert>
        )}

        <Button type="submit" block size="lg" loading={status === "working"} disabled={!ready}>
          {linkMode
            ? status === "working"
              ? "Sending link…"
              : "Email me a sign-in link"
            : status === "working"
              ? "Signing in…"
              : "Sign in"}
        </Button>
      </form>

      <button
        type="button"
        className="link mt-6 text-sm"
        onClick={() => switchTo(linkMode ? "password" : "link")}
      >
        {linkMode ? "Sign in with a password instead" : "Forgot your password? Email me a link"}
      </button>

      <p className="mt-8 text-sm text-muted">
        New to {BRAND.name}?{" "}
        <Link href="/get-started" className="link">
          Set up your receptionist
        </Link>
      </p>
    </div>
  );
}
