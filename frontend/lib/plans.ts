/**
 * The plans, as the product presents them.
 *
 * Mirrors `PLAN_CAPABILITIES` in `backend/app/models/billing.py`, which is the
 * single definition of what a plan includes. There is no plans endpoint, so
 * the two are kept in step by hand — change a number there, change it here.
 *
 * **Only what the product actually does is advertised.** The backend table
 * also carries `max_numbers`, `max_members` and the `custom_voice`,
 * `api_access`, `sso` and `priority_support` flags, but nothing in the product
 * implements them yet: provisioning sets up one number, there is no team
 * invitation flow, and sign-in is by email link only. They are left out here so
 * a prospect is never sold something that does not exist. Add a feature to
 * `SHIPPED_FEATURES` the day it ships.
 *
 * **There are no prices in the codebase.** `price` is `null` for every plan
 * and the UI renders a "talk to us" call to action instead of inventing a
 * figure. Setting a `price` here is the whole change needed to show one.
 */

import { humanize } from "./format";
import type { Plan } from "./types";

export type PlanFeature = "call_summaries" | "email_notifications";

export interface PlanPrice {
  amount: number;
  currency: string;
  interval: "month" | "year";
}

export interface PlanDefinition {
  id: Plan;
  name: string;
  summary: string;
  includedMinutes: number;
  features: readonly PlanFeature[];
  price: PlanPrice | null;
  recommended?: boolean;
}

export const FEATURE_LABELS: Record<PlanFeature, string> = {
  call_summaries: "AI call summaries",
  email_notifications: "Email notifications",
};

/** Every feature the product ships, in display order. */
export const ALL_FEATURES: readonly PlanFeature[] = ["call_summaries", "email_notifications"];

export const PLAN_CATALOG: readonly PlanDefinition[] = [
  {
    id: "starter",
    name: "Starter",
    summary: "For a single location that wants every call answered.",
    includedMinutes: 500,
    features: ALL_FEATURES,
    price: null,
  },
  {
    id: "pro",
    name: "Pro",
    summary: "For busy teams handling a steady stream of calls.",
    includedMinutes: 2_000,
    features: ALL_FEATURES,
    price: null,
    recommended: true,
  },
  {
    id: "enterprise",
    name: "Enterprise",
    summary: "For multi-location businesses with high call volume.",
    includedMinutes: 10_000,
    features: ALL_FEATURES,
    price: null,
  },
];

/** New signups start on a trial; mirrors `trial_included_minutes`. */
export const TRIAL_INCLUDED_MINUTES = 60;

export function planById(id: string | null | undefined): PlanDefinition | undefined {
  return PLAN_CATALOG.find((plan) => plan.id === id);
}

export function planName(id: string | null | undefined): string {
  if (id === "trial") return "Free trial";
  return planById(id)?.name ?? humanize(id);
}

export function formatPrice(price: PlanPrice): string {
  const amount = new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: price.currency,
    maximumFractionDigits: 0,
  }).format(price.amount);
  return `${amount}/${price.interval === "month" ? "mo" : "yr"}`;
}
