/**
 * The sections of the Agent area.
 *
 * All nine are listed, including the ones that are not built. Hiding them
 * would make the nav look finished and leave a customer hunting for a feature
 * that does not exist; listing them with a plain "not built yet" panel is the
 * honest version, and it keeps the shape of the page stable as each one lands.
 *
 * `built: false` is load-bearing — the frame refuses to render a panel for an
 * unbuilt section, so adding a nav entry without an implementation cannot
 * accidentally ship an empty page that looks like a broken one.
 */

import type { ComponentType } from "react";

import {
  BookIcon,
  CalendarIcon,
  FileTextIcon,
  MessageIcon,
  PhoneForwardIcon,
  PlugIcon,
  SlidersIcon,
  TagIcon,
  ZapIcon,
  type IconProps,
} from "@/components/ui/icons";

export type AgentSectionId =
  | "general"
  | "training"
  | "transfer"
  | "texts"
  | "scheduling"
  | "actions"
  | "fields"
  | "tags"
  | "integrations";

export interface AgentSection {
  id: AgentSectionId;
  label: string;
  /** Drawn beside the label. Decorative: the label is already the name. */
  icon: ComponentType<IconProps>;
  /** Whether this section has an implementation behind it. */
  built: boolean;
  /** Shown on the placeholder panel, so "not yet" still says what it will do. */
  summary: string;
  /** Where the capability lives today, when it lives somewhere already. */
  elsewhere?: { label: string; href: string };
}

export const AGENT_SECTIONS: readonly AgentSection[] = [
  {
    id: "general",
    icon: SlidersIcon,
    label: "General",
    built: true,
    summary: "The agent's name, its timezone and the number callers reach it on.",
  },
  {
    id: "training",
    icon: BookIcon,
    label: "Training",
    built: true,
    summary: "What the receptionist knows, how it greets callers and when it escalates.",
  },
  {
    id: "transfer",
    icon: PhoneForwardIcon,
    label: "Transfer calls",
    built: true,
    summary: "Who a caller is handed to when they need a person, and when that happens.",
  },
  {
    id: "texts",
    icon: MessageIcon,
    label: "Send texts",
    built: false,
    summary:
      "Let the receptionist text a caller during or after a call. The message templates are already stored, but nothing is sent: that needs a registered A2P 10DLC campaign.",
    elsewhere: { label: "SMS Campaigns", href: "/dashboard/sms" },
  },
  {
    id: "scheduling",
    icon: CalendarIcon,
    label: "Scheduling",
    built: false,
    summary:
      "Book appointments into a real calendar from a call. Needs the calendar integration to be connected first.",
    elsewhere: { label: "Integrations", href: "/dashboard/integrations" },
  },
  {
    id: "actions",
    icon: ZapIcon,
    label: "Custom actions",
    built: false,
    summary: "Give the receptionist extra things it can do mid-call, beyond taking a message.",
  },
  {
    id: "fields",
    icon: FileTextIcon,
    label: "Custom fields",
    built: false,
    summary:
      "Extra details to collect from every caller. The website setup flow already chooses which questions the agent asks; this would make that editable per agent.",
  },
  {
    id: "tags",
    icon: TagIcon,
    label: "Tags",
    built: true,
    summary: "Label calls by what they were about, so they can be grouped and filtered.",
  },
  {
    id: "integrations",
    icon: PlugIcon,
    label: "Integrations",
    built: false,
    summary: "Connect this agent to the tools the business already uses.",
    elsewhere: { label: "Integrations", href: "/dashboard/integrations" },
  },
];

const BY_ID = new Map(AGENT_SECTIONS.map((section) => [section.id, section]));

/** Falls back to General for an unknown or missing `?section=`. */
export function sectionFor(value: string | null | undefined): AgentSection {
  return (value && BY_ID.get(value as AgentSectionId)) || AGENT_SECTIONS[0]!;
}
