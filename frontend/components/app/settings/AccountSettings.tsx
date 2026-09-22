"use client";

/**
 * Account: who you are, how you sign in, and this device's session.
 *
 * The password form follows the API's rules: the current password is required
 * whenever the account already has one, and a change signs every other device
 * out — the page says so before the button is pressed, not after. A session
 * opened by support staff cannot change either; the API refuses, and the page
 * does not offer it.
 */

import { useState, type FormEvent, type ReactNode } from "react";

import { ThemeToggle } from "@/app/ThemeToggle";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Field";
import { LogOutIcon } from "@/components/ui/icons";
import { useToast } from "@/components/ui/Toast";
import { ApiError, changePassword, updateAccount } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import { humanize } from "@/lib/format";

const MIN_PASSWORD = 8;

export function AccountSettings() {
  const { session, tenantId, switchTenant, signOut } = useTenantData();
  const toast = useToast();
  const membership = session.memberships.find((entry) => entry.tenant_id === tenantId);
  const [signingOut, setSigningOut] = useState(false);

  const [name, setName] = useState(session.full_name ?? "");
  const [savedName, setSavedName] = useState(session.full_name ?? "");
  const [savingName, setSavingName] = useState(false);
  const [hasPassword, setHasPassword] = useState(Boolean(session.has_password));

  async function saveName(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setSavingName(true);
    try {
      const updated = await updateAccount(name.trim());
      setSavedName(updated.full_name ?? "");
      setName(updated.full_name ?? "");
      toast({ tone: "success", title: "Name updated" });
    } catch (caught) {
      toast({
        tone: "danger",
        title: "Couldn't update your name",
        description: caught instanceof ApiError ? caught.message : "Please try again.",
      });
    } finally {
      setSavingName(false);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Profile" />
        <form className="space-y-4 p-5" onSubmit={saveName} noValidate>
          <TextField
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoComplete="name"
            disabled={session.impersonated}
          />
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <Detail label="Email">{session.email}</Detail>
            <Detail label="Role">{membership ? humanize(membership.role) : "—"}</Detail>
          </dl>
          {!session.impersonated && (
            <div className="flex justify-end">
              <Button
                type="submit"
                size="sm"
                loading={savingName}
                disabled={!name.trim() || name.trim() === savedName}
              >
                Save name
              </Button>
            </div>
          )}
        </form>
      </Card>

      <PasswordCard
        hasPassword={hasPassword}
        impersonated={session.impersonated}
        onChanged={() => setHasPassword(true)}
      />

      {session.memberships.length > 1 && (
        <Card className="lg:col-span-2">
          <CardHeader title="Your organizations" description="Switch between the businesses you can access." />
          <ul className="divide-y divide-line">
            {session.memberships.map((entry) => (
              <li key={entry.tenant_id} className="flex items-center justify-between gap-4 px-5 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold">{entry.name}</p>
                  <p className="text-xs text-muted">{humanize(entry.role)}</p>
                </div>
                {entry.tenant_id === tenantId ? (
                  <Badge tone="accent">Current</Badge>
                ) : (
                  <Button
                    size="sm"
                    variant="secondary"
                    aria-label={`Switch to ${entry.name}`}
                    onClick={() => switchTenant(entry.tenant_id)}
                  >
                    Switch
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <CardHeader title="Appearance" description="Light or dark, remembered in this browser." />
        <div className="flex items-center justify-between gap-4 px-5 py-4">
          <p className="text-sm text-ink-2">Theme</p>
          <ThemeToggle />
        </div>
      </Card>

      <Card>
        <CardHeader title="Session" />
        <div className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted">Sign out of {BRAND.name} on this device.</p>
          <Button
            variant="secondary"
            leadingIcon={<LogOutIcon size={16} />}
            loading={signingOut}
            onClick={async () => {
              setSigningOut(true);
              await signOut();
              setSigningOut(false);
            }}
          >
            Sign out
          </Button>
        </div>
      </Card>
    </div>
  );
}

function PasswordCard({
  hasPassword,
  impersonated,
  onChanged,
}: {
  hasPassword: boolean;
  impersonated: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<{ field: "current" | "next" | "confirm"; message: string } | null>(
    null,
  );
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (hasPassword && !current) {
      setError({ field: "current", message: "Enter your current password." });
      return;
    }
    if (next.length < MIN_PASSWORD) {
      setError({ field: "next", message: `Use at least ${MIN_PASSWORD} characters.` });
      return;
    }
    if (next !== confirm) {
      setError({ field: "confirm", message: "The two passwords don't match." });
      return;
    }
    setSaving(true);
    try {
      await changePassword(hasPassword ? current : null, next);
      toast({
        tone: "success",
        title: hasPassword ? "Password changed" : "Password set",
        description: "You've been signed out on your other devices.",
      });
      onChanged();
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (caught) {
      const failure = caught instanceof ApiError ? caught : null;
      if (failure?.code === "invalid_current_password") {
        setError({ field: "current", message: "That isn't your current password." });
      } else {
        toast({
          tone: "danger",
          title: "Couldn't change your password",
          description: failure?.message ?? "Please try again.",
        });
      }
      setCurrent("");
    } finally {
      setSaving(false);
    }
  }

  if (impersonated) {
    return (
      <Card>
        <CardHeader title="Password" />
        <p className="p-5 text-sm text-muted">Passwords can only be changed by the account&apos;s owner.</p>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Password"
        description={
          hasPassword
            ? "Changing it signs you out on your other devices."
            : "You sign in with email links. Set a password to sign in without waiting for an email."
        }
      />
      <form className="space-y-4 p-5" onSubmit={submit} noValidate>
        {hasPassword && (
          <TextField
            label="Current password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            error={error?.field === "current" ? error.message : undefined}
          />
        )}
        <TextField
          label="New password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(event) => setNext(event.target.value)}
          hint={`At least ${MIN_PASSWORD} characters.`}
          error={error?.field === "next" ? error.message : undefined}
        />
        <TextField
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          error={error?.field === "confirm" ? error.message : undefined}
        />
        <div className="flex justify-end">
          <Button type="submit" size="sm" loading={saving} disabled={!next}>
            {hasPassword ? "Change password" : "Set password"}
          </Button>
        </div>
      </form>
    </Card>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 font-medium break-words text-ink">{children}</dd>
    </div>
  );
}
