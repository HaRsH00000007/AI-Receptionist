"use client";

/**
 * Create an account — the first step of getting started, before any business.
 *
 * Three fields and nothing else, so starting costs a minute. The account is
 * signed in the moment it is created (an HttpOnly cookie the page never
 * sees), and the business form follows — saved as it is filled in, so it can
 * be finished later from any device.
 *
 * This form, unlike sign-in, does say when an address already has an account:
 * a create-account form cannot usefully do otherwise. The API rate-limits it.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Field";
import { ApiError, getSessionOrNull, registerAccount } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import { ONBOARDING_PATH, signupRedirect } from "@/lib/routing";

/** Mirrors `MIN_LENGTH` in `app/services/passwords.py`. */
const PASSWORD_MIN_LENGTH = 8;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type Field = "name" | "email" | "password";

export function SignupForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({});
  const [failure, setFailure] = useState<{ message: string; exists: boolean } | null>(null);
  const [working, setWorking] = useState(false);

  // Someone already signed in has an account: continue to the business form.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const target = signupRedirect(await getSessionOrNull());
        if (target && !cancelled) router.replace(target);
      } catch {
        // Unreachable API: the form reports it on submit.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  function validate(): boolean {
    const found: Partial<Record<Field, string>> = {};
    if (!name.trim()) found.name = "Enter your name.";
    if (!EMAIL.test(email.trim())) found.email = "Enter a valid email address.";
    // Length only, matching the backend; not trimmed, matching the login form.
    if (password.length < PASSWORD_MIN_LENGTH) {
      found.password = `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
    }
    setErrors(found);
    return Object.keys(found).length === 0;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setFailure(null);
    if (!validate()) return;
    setWorking(true);
    try {
      await registerAccount(name.trim(), email.trim(), password);
      // A full navigation: the next page is rendered against the new cookie.
      router.replace(ONBOARDING_PATH);
      router.refresh();
    } catch (caught) {
      const error = caught instanceof ApiError ? caught : null;
      setPassword("");
      setWorking(false);
      if (error?.code === "account_exists") {
        setFailure({ message: "An account with this email already exists.", exists: true });
      } else if (error && Object.keys(error.fieldErrors).length) {
        setErrors({
          email: error.fieldErrors.email,
          password: error.fieldErrors.password,
          name: error.fieldErrors.full_name,
        });
      } else {
        setFailure({
          message: error?.message ?? "Something went wrong. Please try again.",
          exists: false,
        });
      }
    }
  }

  return (
    <div>
      <h1 className="text-[1.75rem] font-bold tracking-tight">Create your account</h1>
      <p className="mt-2 text-[0.9375rem] text-muted">
        Start with an account. Next, tell us about your business and your receptionist is set up in
        minutes.
      </p>

      <form className="mt-8 space-y-5" noValidate onSubmit={submit}>
        <TextField
          label="Your name"
          name="name"
          autoComplete="name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Dana Rivera"
          error={errors.name}
        />
        <TextField
          label="Email address"
          name="email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@yourbusiness.com"
          hint="You'll sign in with this."
          error={errors.email}
        />
        <div>
          <TextField
            label="Password"
            name="password"
            type={revealed ? "text" : "password"}
            // `new-password` so a password manager offers to generate one.
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder={`At least ${PASSWORD_MIN_LENGTH} characters`}
            error={errors.password}
          />
          <button type="button" className="link mt-2 text-xs" onClick={() => setRevealed(!revealed)}>
            {revealed ? "Hide password" : "Show password"}
          </button>
        </div>

        {failure && (
          <Alert tone="danger" title={failure.message}>
            {failure.exists ? (
              <>
                <Link href="/login" className="link">
                  Sign in instead
                </Link>
                , or use a different email.
              </>
            ) : null}
          </Alert>
        )}

        <Button type="submit" block size="lg" loading={working}>
          {working ? "Creating your account…" : "Create account"}
        </Button>
      </form>

      <p className="mt-8 text-sm text-muted">
        Already have a {BRAND.name} account?{" "}
        <Link href="/login" className="link">
          Sign in
        </Link>
      </p>
    </div>
  );
}
