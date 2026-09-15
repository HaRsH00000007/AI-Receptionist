import { BRAND } from "@/lib/brand";

export const NAV_LINKS = [
  { href: "/#features", label: "Features" },
  { href: "/#industries", label: "Industries" },
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#pricing", label: "Pricing" },
  { href: "/help", label: "Resources" },
] as const;

/** A real inbox when one is configured; otherwise the contact page, never a dead address. */
export const TALK_TO_US_HREF = BRAND.supportEmail ? `mailto:${BRAND.supportEmail}` : "/contact";
