"use client";

/**
 * The signed-in application frame: sidebar on desktop, a top bar everywhere,
 * and a bottom tab bar plus slide-out menu on mobile.
 *
 * The shell also owns the workspace's loading and error states, so every page
 * inside it can assume its data is present.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useId, useState, type ReactNode } from "react";

import { ThemeToggle } from "@/app/ThemeToggle";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { cx } from "@/components/ui/cx";
import { Dialog } from "@/components/ui/Dialog";
import { Skeleton } from "@/components/ui/Feedback";
import {
  BellIcon,
  ChevronDownIcon,
  LifebuoyIcon,
  LogOutIcon,
  MenuIcon,
  SettingsIcon,
} from "@/components/ui/icons";
import { Logo } from "@/components/ui/Logo";
import { Menu, type MenuItem } from "@/components/ui/Menu";
import { formatNumber, initials, pluralize } from "@/lib/format";
import { planName } from "@/lib/plans";
import { RECEPTIONIST_STATE, isUrgent, receptionistState } from "@/lib/status";

import { ACCOUNT_NAV, MOBILE_TABS, PRIMARY_NAV, isActive, type NavItem } from "./navigation";
import { useWorkspace, type TenantData } from "./TenantWorkspace";

export function AppShell({ children }: { children: ReactNode }) {
  const { session } = useWorkspace();
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[16.5rem_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-dvh border-r border-line bg-surface lg:block">
        <Sidebar />
      </aside>

      <div className="flex min-w-0 flex-col">
        <TopBar onOpenNav={() => setNavOpen(true)} />
        {session.impersonated && (
          <div
            role="alert"
            className="border-b border-warn-line bg-warn-soft px-4 py-2.5 text-center text-sm font-medium text-warn-ink sm:px-6"
          >
            You are viewing this account as support staff. This session is audited.
          </div>
        )}
        <main id="main" className="flex-1 px-4 pt-6 pb-28 sm:px-6 lg:px-8 lg:pt-8 lg:pb-12">
          <div className="mx-auto w-full max-w-6xl">
            <WorkspaceBody>{children}</WorkspaceBody>
          </div>
        </main>
      </div>

      <MobileTabBar onMore={() => setNavOpen(true)} />

      <Dialog open={navOpen} onClose={() => setNavOpen(false)} variant="sheet" title="Menu">
        <div className="-mx-5 -my-5 h-full sm:-mx-6">
          <Sidebar onNavigate={() => setNavOpen(false)} />
        </div>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Body states
// ---------------------------------------------------------------------------

function WorkspaceBody({ children }: { children: ReactNode }) {
  const { data, loading, error, refresh, refreshing } = useWorkspace();

  if (loading && !data) return <WorkspaceSkeleton />;

  if (!data) {
    return (
      <div className="mx-auto max-w-lg py-16">
        <Alert
          tone="danger"
          title="We couldn't load your dashboard"
          action={
            <Button size="sm" variant="secondary" loading={refreshing} onClick={refresh}>
              Try again
            </Button>
          }
        >
          {error ?? "Could not load your dashboard."}
        </Alert>
      </div>
    );
  }

  return (
    <>
      {error && (
        <Alert
          tone="warn"
          className="mb-6"
          title="Connection interrupted"
          action={
            <Button size="sm" variant="secondary" loading={refreshing} onClick={refresh}>
              Retry
            </Button>
          }
        >
          Showing the last information we loaded. {error}
        </Alert>
      )}
      {children}
    </>
  );
}

function WorkspaceSkeleton() {
  return (
    <div aria-busy="true">
      <p role="status" className="sr-only">
        Loading your receptionist…
      </p>
      <Skeleton className="h-8 w-56" />
      <Skeleton className="mt-3 h-4 w-80 max-w-full" />
      <Skeleton className="mt-8 h-48 w-full rounded-2xl" />
      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((key) => (
          <Skeleton key={key} className="h-28 rounded-xl" />
        ))}
      </div>
      <Skeleton className="mt-6 h-72 rounded-2xl" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname() ?? "";
  const { session, tenantId, switchTenant, data } = useWorkspace();
  const switcherId = useId();
  const memberships = session.memberships;
  const current = memberships.find((membership) => membership.tenant_id === tenantId);

  return (
    <div className="flex h-full flex-col">
      <div className="px-5 pt-5 pb-5">
        <Logo href="/dashboard" />
      </div>

      <div className="px-3">
        {memberships.length > 1 ? (
          <div className="px-2">
            <label htmlFor={switcherId} className="text-xs font-semibold text-muted">
              Organization
            </label>
            <select
              id={switcherId}
              className="field !mt-1.5 !min-h-10 !text-sm"
              value={tenantId}
              onChange={(event) => switchTenant(event.target.value)}
            >
              {memberships.map((membership) => (
                <option key={membership.tenant_id} value={membership.tenant_id}>
                  {membership.name}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div className="flex items-center gap-3 rounded-xl border border-line bg-surface-2 px-3 py-2.5">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-line bg-surface text-xs font-bold text-ink-2">
              {initials(current?.name ?? data?.tenant.name)}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{current?.name ?? data?.tenant.name}</p>
              <p className="truncate text-xs text-muted">{data ? planName(data.tenant.plan) : "—"}</p>
            </div>
          </div>
        )}
      </div>

      <nav aria-label="Dashboard" className="mt-5 flex-1 overflow-y-auto px-3 pb-4">
        <NavList items={PRIMARY_NAV} pathname={pathname} onNavigate={onNavigate} />
        <p className="mt-6 mb-1.5 px-3 text-[0.6875rem] font-semibold tracking-[0.08em] text-subtle uppercase">
          Account
        </p>
        <NavList items={ACCOUNT_NAV} pathname={pathname} onNavigate={onNavigate} />
      </nav>

      {data && <SidebarUsage data={data} onNavigate={onNavigate} />}
    </div>
  );
}

function NavList({
  items,
  pathname,
  onNavigate,
}: {
  items: readonly NavItem[];
  pathname: string;
  onNavigate?: () => void;
}) {
  return (
    <ul className="space-y-0.5">
      {items.map((item) => {
        const active = isActive(pathname, item);
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              aria-current={active ? "page" : undefined}
              onClick={onNavigate}
              className={cx(
                "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                active ? "bg-accent-soft text-accent-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
              )}
            >
              <item.icon
                size={18}
                className={cx(active ? "text-accent" : "text-subtle group-hover:text-ink-2")}
              />
              {item.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function SidebarUsage({ data, onNavigate }: { data: TenantData; onNavigate?: () => void }) {
  const { usage } = data;
  const percent = Math.min(100, usage.percent_used);
  return (
    <Link
      href="/dashboard/usage"
      onClick={onNavigate}
      className="m-3 block rounded-xl border border-line p-3.5 transition-colors hover:bg-surface-2"
    >
      <span className="flex items-baseline justify-between gap-2 text-xs">
        <span className="font-semibold text-ink-2">Minutes this period</span>
        <span className="tabular-nums text-muted">{usage.percent_used}%</span>
      </span>
      <span className="progress progress-sm mt-2 block" aria-hidden>
        <span
          className={cx(
            "progress-fill block",
            usage.over_limit ? "progress-danger" : usage.warning ? "progress-warn" : "progress-accent",
          )}
          style={{ width: `${percent}%` }}
        />
      </span>
      <span className="mt-2 block text-xs text-muted">
        {formatNumber(usage.call_minutes)} of {formatNumber(usage.included_minutes)} min ·{" "}
        {planName(usage.plan)}
      </span>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Top bar
// ---------------------------------------------------------------------------

function TopBar({ onOpenNav }: { onOpenNav: () => void }) {
  const { session, data, signOut } = useWorkspace();
  const state = data ? receptionistState(data.tenant.status, data.provisioning.status) : null;
  const meta = state ? RECEPTIONIST_STATE[state] : null;

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-bg/85 backdrop-blur-md">
      <div className="flex h-16 items-center gap-2 px-4 sm:gap-3 sm:px-6 lg:px-8">
        <Button
          variant="ghost"
          iconOnly
          aria-label="Open navigation"
          onClick={onOpenNav}
          className="-ml-2 lg:hidden"
        >
          <MenuIcon size={20} />
        </Button>

        <div className="flex min-w-0 flex-1 items-center gap-3">
          <span className="truncate text-[0.9375rem] font-semibold">{data?.tenant.name}</span>
          {meta && (
            <Badge tone={meta.tone} dot pulse={state === "live"} className="hidden sm:inline-flex">
              {state === "live" ? "Receptionist live" : meta.label}
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-1">
          {data && <NotificationsMenu data={data} />}
          <ThemeToggle className="hidden sm:inline-flex" />
          <Menu
            label="Account menu"
            triggerClassName="!px-1.5"
            trigger={
              <>
                <span className="flex size-8 items-center justify-center rounded-full bg-ink text-xs font-bold text-bg">
                  {initials(session.full_name ?? session.email)}
                </span>
                <ChevronDownIcon size={15} className="hidden text-muted sm:block" />
              </>
            }
            header={
              <>
                <p className="truncate text-sm font-semibold">{session.full_name ?? "Signed in"}</p>
                <p className="truncate text-xs text-muted">{session.email}</p>
              </>
            }
            items={[
              { label: "Settings", icon: <SettingsIcon size={16} />, href: "/dashboard/settings" },
              { label: "Help center", icon: <LifebuoyIcon size={16} />, href: "/help" },
              {
                label: "Sign out",
                icon: <LogOutIcon size={16} />,
                tone: "danger",
                onSelect: () => void signOut(),
              },
            ]}
          />
        </div>
      </div>
    </header>
  );
}

/** Notifications derived from state the API already reports — nothing invented. */
function notificationsFor(data: TenantData): MenuItem[] {
  const items: MenuItem[] = [];
  const state = receptionistState(data.tenant.status, data.provisioning.status);

  if (state === "attention") {
    items.push({
      label: "Setup needs attention",
      description: "Setup stopped before your receptionist went live.",
      href: "/dashboard/receptionist",
    });
  } else if (state === "billing") {
    items.push({
      label: "Setup is waiting on billing",
      description: "It continues automatically once your plan is active.",
      href: "/dashboard/billing",
    });
  } else if (state === "setting_up") {
    items.push({
      label: "Your receptionist is being set up",
      description: `${data.provisioning.completed_steps} of ${data.provisioning.total_steps} steps done.`,
      href: "/dashboard/receptionist",
    });
  }

  if (data.usage.over_limit) {
    items.push({
      label: "You've used all included minutes",
      description: "See your usage for this period.",
      href: "/dashboard/usage",
    });
  } else if (data.usage.warning) {
    items.push({
      label: `You've used ${data.usage.percent_used}% of your minutes`,
      description: "See your usage for this period.",
      href: "/dashboard/usage",
    });
  }

  const urgent = data.calls.filter((call) => isUrgent(call.urgency)).length;
  if (urgent > 0) {
    items.push({
      label: `${pluralize(urgent, "urgent call")} in your recent calls`,
      description: "Review what callers needed.",
      href: "/dashboard/calls?urgent=1",
    });
  }

  const unsummarized = data.calls.filter((call) => call.status === "failed").length;
  if (unsummarized > 0) {
    items.push({
      label: `${pluralize(unsummarized, "call")} couldn't be summarized`,
      description: "Open them to see what was captured.",
      href: "/dashboard/calls?status=failed",
    });
  }

  return items;
}

function NotificationsMenu({ data }: { data: TenantData }) {
  const items = notificationsFor(data);
  return (
    <Menu
      label={items.length ? `Notifications (${items.length})` : "Notifications"}
      iconOnly
      width="w-80"
      trigger={
        <span className="relative">
          <BellIcon size={19} />
          {items.length > 0 && (
            <span className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-danger ring-2 ring-bg" />
          )}
        </span>
      }
      header={<p className="text-sm font-semibold">Notifications</p>}
      items={items.length ? items : [{ label: "You're all caught up", disabled: true }]}
    />
  );
}

// ---------------------------------------------------------------------------
// Mobile
// ---------------------------------------------------------------------------

function MobileTabBar({ onMore }: { onMore: () => void }) {
  const pathname = usePathname() ?? "";
  return (
    <nav
      aria-label="Quick navigation"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md lg:hidden"
    >
      <ul className="grid grid-cols-5">
        {MOBILE_TABS.map((item) => {
          const active = isActive(pathname, item);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cx(
                  "flex flex-col items-center gap-1 py-2.5 text-[0.6875rem] font-semibold",
                  active ? "text-accent" : "text-muted",
                )}
              >
                <item.icon size={20} />
                {item.shortLabel ?? item.label}
              </Link>
            </li>
          );
        })}
        <li>
          <button
            type="button"
            onClick={onMore}
            className="flex w-full flex-col items-center gap-1 py-2.5 text-[0.6875rem] font-semibold text-muted"
          >
            <MenuIcon size={20} />
            More
          </button>
        </li>
      </ul>
    </nav>
  );
}
