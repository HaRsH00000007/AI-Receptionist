"use client";

/**
 * Settings: one heading, and a section switcher that is a real list of links —
 * each section is its own route, so a link (from Home, from a notification) can
 * open the one that matters.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { cx } from "@/components/ui/cx";
import { PageHeader } from "@/components/ui/PageHeader";

import { SETTINGS_SECTIONS, isActive } from "../navigation";

export function SettingsFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "";
  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Your account, your business and your receptionist." />
      <nav aria-label="Settings sections" className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
        <ul className="flex min-w-max gap-1 border-b border-line">
          {SETTINGS_SECTIONS.map((section) => {
            const active = isActive(pathname, section);
            return (
              <li key={section.href}>
                <Link
                  href={section.href}
                  aria-current={active ? "page" : undefined}
                  className={cx(
                    "-mb-px block border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
                    active
                      ? "border-accent text-ink"
                      : "border-transparent text-muted hover:text-ink",
                  )}
                >
                  {section.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <div>{children}</div>
    </div>
  );
}
