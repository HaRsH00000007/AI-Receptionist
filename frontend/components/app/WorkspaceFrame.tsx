"use client";

/**
 * Chooses the frame for a signed-in tenant, and keeps them on the right page.
 *
 * A live receptionist gets the full application — sidebar, every section — and
 * is never shown setup again. A tenant still being set up, waiting on billing,
 * stopped on an error or no longer active gets a focused setup page instead of
 * a dashboard full of empty sections. The rules themselves live in
 * `lib/routing.ts`; this only applies them.
 *
 * Until the tenant has loaded, the shell renders its own loading and error
 * states, so nothing tenant-shaped appears before the data does.
 */

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";

import { ThemeToggle } from "@/app/ThemeToggle";
import { Button } from "@/components/ui/Button";
import { LogOutIcon } from "@/components/ui/icons";
import { Logo } from "@/components/ui/Logo";
import { Spinner } from "@/components/ui/Spinner";
import { NEW_BUSINESS_PATH, redirectFor, stageOf } from "@/lib/routing";

import { AppShell } from "./AppShell";
import { useWorkspace } from "./TenantWorkspace";

export function WorkspaceFrame({ children }: { children: ReactNode }) {
  const { data, session, switchTenant } = useWorkspace();
  const pathname = usePathname() ?? "";
  const router = useRouter();

  const stage = data ? stageOf(data.tenant.status, data.provisioning.status) : null;

  // Businesses already found cancelled. Without this, an account whose
  // businesses are *all* cancelled ping-pongs between them forever: each one
  // says "you own another, open that instead" about the other, and every hop
  // refetches seven endpoints. Remembering which have been tried makes the
  // search terminate — at worst one switch per membership, then the form.
  const tried = useRef<Set<string>>(new Set());
  const target = stage && stage !== "onboarding" ? redirectFor(stage, pathname) : null;

  useEffect(() => {
    if (target) router.replace(target);
  }, [target, router]);

  // The business the loaded data describes — not `tenantId`, which changes the
  // moment a switch starts. Reading the stage from one and the id from the
  // other lets a render disagree with itself and mark the wrong business tried.
  const openedId = data?.tenant.id ?? null;

  useEffect(() => {
    if (stage !== "onboarding" || !openedId) return;
    tried.current.add(openedId);
    // Someone whose open business is cancelled may still own a live one.
    // Opening that beats offering to create a third — but only ones we have
    // not already looked at.
    const next = session.memberships.find(
      (membership) =>
        membership.tenant_id !== openedId && !tried.current.has(membership.tenant_id),
    )?.tenant_id;
    if (next) switchTenant(next);
    else router.replace(NEW_BUSINESS_PATH);
  }, [stage, openedId, session.memberships, switchTenant, router]);

  if (!data) return <AppShell>{children}</AppShell>;

  if (target || stage === "onboarding") {
    return (
      <main id="main" className="flex min-h-dvh flex-col items-center justify-center gap-3 px-4">
        <Spinner className="size-5 text-accent" />
        <p role="status" className="text-sm text-muted">
          {stage === "dashboard"
            ? "Opening your dashboard…"
            : stage === "onboarding"
              ? "Taking you to set up a receptionist…"
              : "Opening your setup…"}
        </p>
      </main>
    );
  }

  if (stage === "setup") return <SetupFrame>{children}</SetupFrame>;
  return <AppShell>{children}</AppShell>;
}

function SetupFrame({ children }: { children: ReactNode }) {
  const { data, signOut } = useWorkspace();
  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-30 border-b border-line bg-bg/85 backdrop-blur-md">
        <div className="mx-auto flex h-16 w-full max-w-4xl items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Logo href="/dashboard" />
            {data && (
              <span className="hidden truncate border-l border-line pl-3 text-sm font-medium text-muted sm:inline">
                {data.tenant.name}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <Button
              variant="ghost"
              size="sm"
              leadingIcon={<LogOutIcon size={16} />}
              onClick={() => void signOut()}
            >
              Sign out
            </Button>
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 sm:py-12">
        {children}
      </main>
    </div>
  );
}
