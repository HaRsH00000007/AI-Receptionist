import type { ComponentType } from "react";

import {
  GridIcon,
  HeadsetIcon,
  ListIcon,
  PlugIcon,
  SendIcon,
  SettingsIcon,
  UsersIcon,
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
  { href: "/dashboard", label: "Home", icon: GridIcon, exact: true },
  { href: "/dashboard/calls", label: "Calls", icon: ListIcon },
  { href: "/dashboard/agent", label: "Agent", icon: HeadsetIcon },
  { href: "/dashboard/contacts", label: "Contacts", icon: UsersIcon },
  { href: "/dashboard/sms", label: "SMS Campaigns", shortLabel: "SMS", icon: SendIcon },
  { href: "/dashboard/integrations", label: "Integrations", icon: PlugIcon },
];

export const ACCOUNT_NAV: readonly NavItem[] = [
  { href: "/dashboard/settings", label: "Settings", icon: SettingsIcon },
];

/** The four destinations worth a thumb on mobile; the rest live behind "More". */
export const MOBILE_TABS: readonly NavItem[] = [
  PRIMARY_NAV[0]!,
  PRIMARY_NAV[1]!,
  PRIMARY_NAV[2]!,
  PRIMARY_NAV[3]!,
];

export function isActive(pathname: string, item: NavItem): boolean {
  if (item.exact) return pathname === item.href;
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}

/** The sections of Settings, each its own route so a link can open one. */
export const SETTINGS_SECTIONS: readonly NavItem[] = [
  { href: "/dashboard/settings", label: "Account", icon: SettingsIcon, exact: true },
  { href: "/dashboard/settings/business", label: "Business", icon: SettingsIcon },
  { href: "/dashboard/settings/receptionist", label: "Receptionist", icon: SettingsIcon },
  { href: "/dashboard/settings/phone", label: "Phone", icon: SettingsIcon },
  { href: "/dashboard/settings/notifications", label: "Notifications", icon: SettingsIcon },
  { href: "/dashboard/settings/billing", label: "Billing", icon: SettingsIcon },
];
