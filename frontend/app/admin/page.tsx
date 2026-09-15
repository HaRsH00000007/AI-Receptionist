"use client";

/**
 * The admin route.
 *
 * Asks for the operator key and holds it in memory only. Deliberately not
 * localStorage: this credential authorizes actions that release phone numbers,
 * and a page that persisted it would let any script running on this origin —
 * now or after a future XSS bug — take those actions long after the operator
 * walked away.
 *
 * The cost is re-entering the key after a reload. That is the right trade for
 * a page used a handful of times a week by a handful of people.
 */

import { useCallback, useState } from "react";

import { ThemeToggle } from "@/app/ThemeToggle";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Field";
import { LockIcon } from "@/components/ui/icons";
import { Logo } from "@/components/ui/Logo";

import AdminView from "./AdminView";

export default function AdminPage() {
  const [adminKey, setAdminKey] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [rejected, setRejected] = useState(false);

  const onUnauthorized = useCallback(() => {
    setSubmitted(null);
    setRejected(true);
  }, []);

  // Locking drops the typed key as well, so the gate does not hand it back.
  const onLock = useCallback(() => {
    setSubmitted(null);
    setAdminKey("");
    setRejected(false);
  }, []);

  if (submitted) {
    return <AdminView adminKey={submitted} onUnauthorized={onUnauthorized} onLock={onLock} />;
  }

  return (
    <main id="main" className="grid min-h-dvh place-items-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center justify-between">
          <Logo href="/" />
          <ThemeToggle />
        </div>

        <div className="card p-6 shadow-md sm:p-7">
          <div className="mb-5 flex size-10 items-center justify-center rounded-xl bg-accent-soft text-accent">
            <LockIcon size={19} />
          </div>
          <h1 className="text-xl font-bold tracking-tight">Operator console</h1>
          <p className="mt-1.5 text-sm text-muted">
            Enter the admin key. It is held in memory for this tab only and is never saved.
          </p>

          <form
            className="mt-6 space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              setRejected(false);
              setSubmitted(adminKey);
            }}
          >
            <TextField
              label="Admin key"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={adminKey}
              onChange={(event) => setAdminKey(event.target.value)}
              aria-invalid={rejected || undefined}
              aria-describedby={rejected ? "admin-key-error" : undefined}
            />

            {rejected && (
              <div id="admin-key-error">
                <Alert tone="danger">That key was refused.</Alert>
              </div>
            )}

            <Button type="submit" block size="lg" disabled={!adminKey}>
              Continue
            </Button>
          </form>
        </div>

        <p className="mt-6 text-center text-xs text-subtle">
          Reloading this page locks the console again.
        </p>
      </div>
    </main>
  );
}
