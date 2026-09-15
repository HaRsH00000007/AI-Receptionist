import type { ComponentType } from "react";

import {
  BarChartIcon,
  CreditCardIcon,
  GridIcon,
  HashIcon,
  HeadsetIcon,
  ListIcon,
  SettingsIcon,
  SlidersIcon,
  type IconProps,
} from "@/components/ui/icons";

export interface NavItem {
  href: string;
  label: string;
  shortLabel?: string;
  icon: ComponentType<IconProps>;
  /** Match the path exactly rather than as a prefix. */
  exact?: boolean;
}

export const PRIMARY_NAV: readonly NavItem[] = [
  { href: "/dashboard", label: "Overview", icon: GridIcon, exact: true },
  { href: "/dashboard/receptionist", label: "AI Receptionist", shortLabel: "Receptionist", icon: HeadsetIcon },
  { href: "/dashboard/calls", label: "Calls", icon: ListIcon },
  { href: "/dashboard/phone", label: "Phone Numbers", shortLabel: "Number", icon: HashIcon },
  { href: "/dashboard/configuration", label: "Configuration", icon: SlidersIcon },
];

export const ACCOUNT_NAV: readonly NavItem[] = [
  { href: "/dashboard/usage", label: "Usage", icon: BarChartIcon },
  { href: "/dashboard/billing", label: "Billing", icon: CreditCardIcon },
  { href: "/dashboard/settings", label: "Settings", icon: SettingsIcon },
];

/** The four destinations worth a thumb on mobile; the rest live behind "More". */
export const MOBILE_TABS: readonly NavItem[] = [
  PRIMARY_NAV[0]!,
  PRIMARY_NAV[2]!,
  PRIMARY_NAV[1]!,
  ACCOUNT_NAV[0]!,
];

export function isActive(pathname: string, item: NavItem): boolean {
  if (item.exact) return pathname === item.href;
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}
