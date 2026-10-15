/**
 * Where a signed-in customer belongs, decided from their tenant's state.
 *
 *     not signed in                 -> /signup             (create account)
 *     signed in, no business yet    -> /get-started        (business form, resumed)
 *     setting up / waiting billing  -> /dashboard/setup    (provisioning status)
 *     setup stopped on an error     -> /dashboard/setup    (retry)
 *     cancelled or abandoned        -> /get-started        (set a new one up)
 *     live                          -> /dashboard          (and never onboarding again)
 *
 * Pure functions, so the rules are testable without rendering anything, and so
 * the gate, the onboarding page and the tests cannot hold three different
 * opinions about them. The server still authorizes every request — this only
 * decides which page to show.
 */

import { receptionistState, type ReceptionistState } from "./status";
import type { ProvisioningStatus } from "./types";

export const DASHBOARD_HOME = "/dashboard";
export const SETUP_PATH = "/dashboard/setup";
export const ONBOARDING_PATH = "/get-started";
/**
 * The setup form, reached deliberately by someone who already has a business —
 * an owner adding another, or one who has just deleted theirs. Without
 * `another` the form would bounce them back to the dashboard.
 */
export const NEW_BUSINESS_PATH = `${ONBOARDING_PATH}?another=1`;
export const LOGIN_PATH = "/login";
export const SIGNUP_PATH = "/signup";

/** The part of the product a tenant in this state should see. */
export type Stage = "dashboard" | "setup" | "onboarding";

export function stageFor(state: ReceptionistState): Stage {
  if (state === "live") return "dashboard";
  // A cancelled or abandoned business has nothing left to watch. The setup
  // page can only say so, and its one button is "set up a receptionist" — so
  // send them there directly rather than through a dead end. This is what
  // someone signing in after deleting their receptionist should see.
  if (state === "inactive") return "onboarding";
  return "setup";
}

export function stageOf(tenantStatus: string, provisioning: ProvisioningStatus | null): Stage {
  return stageFor(receptionistState(tenantStatus, provisioning));
}

function onSetupPage(pathname: string): boolean {
  return pathname === SETUP_PATH || pathname.startsWith(`${SETUP_PATH}/`);
}

/**
 * Where to send someone who is at `pathname`, or `null` to leave them there.
 *
 * A live tenant is kept off the setup page; any other tenant is kept on it.
 * Deep links inside the dashboard are preserved for a live tenant — a link to
 * a particular call still opens that call.
 */
export function redirectFor(stage: Stage, pathname: string): string | null {
  // `another=1` matters: the setup form sends anyone who already belongs to a
  // business back to the dashboard, and this person still has a membership —
  // to the business they just cancelled. Without it the two would bounce.
  if (stage === "onboarding") return NEW_BUSINESS_PATH;
  if (stage === "dashboard") return onSetupPage(pathname) ? DASHBOARD_HOME : null;
  return onSetupPage(pathname) ? null : SETUP_PATH;
}

/**
 * Where the business-setup form should send a visitor, or `null` to show it.
 *
 * Account first: someone not signed in creates an account before seeing the
 * form. Anyone who already belongs to a business goes to the dashboard, which
 * then routes by that business's state — so a live customer is never shown the
 * form again. `another` is the deliberate way through, for an owner setting up
 * a second business.
 */
export function onboardingRedirect(
  session: { memberships: readonly unknown[] } | null,
  another: boolean,
): string | null {
  if (!session) return SIGNUP_PATH;
  if (session.memberships.length === 0 || another) return null;
  return DASHBOARD_HOME;
}

/**
 * Where the create-account page should send a visitor, or `null` to show it.
 * Someone already signed in has an account; they continue to the form.
 */
export function signupRedirect(session: unknown | null): string | null {
  return session ? ONBOARDING_PATH : null;
}

/** Business states that are still somewhere to work. */
const WORKABLE = new Set(["pending", "active", "failed"]);

/**
 * The businesses worth offering an account.
 *
 * A cancelled or abandoned business stays a membership — the account keeps its
 * history — but it is not a place to work, and listing it in the organization
 * switcher only offers a door that leads straight back out again.
 */
export function workableMemberships<T extends { status: string }>(memberships: readonly T[]): T[] {
  return memberships.filter((membership) => WORKABLE.has(membership.status));
}
