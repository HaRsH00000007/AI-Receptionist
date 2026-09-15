"use client";

/**
 * A dropdown menu.
 *
 * Follows the menu-button pattern: the trigger reports `aria-expanded`, focus
 * moves into the menu on open, arrow keys move between items, and Escape or a
 * click elsewhere closes it and returns focus to the trigger.
 */

import Link from "next/link";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { buttonClasses, type ButtonVariant } from "./Button";
import { cx } from "./cx";

export interface MenuItem {
  label: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  href?: string;
  onSelect?: () => void;
  tone?: "danger";
  disabled?: boolean;
}

export function Menu({
  label,
  trigger,
  items,
  header,
  align = "end",
  triggerVariant = "ghost",
  triggerClassName,
  iconOnly = false,
  width = "w-64",
}: {
  /** The accessible name of the trigger. */
  label: string;
  trigger: ReactNode;
  items: MenuItem[];
  header?: ReactNode;
  align?: "start" | "end";
  triggerVariant?: ButtonVariant;
  triggerClassName?: string;
  iconOnly?: boolean;
  width?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    menu?.querySelector<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])')?.focus();

    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
        return;
      }
      if (!menu || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
      event.preventDefault();
      const entries = Array.from(
        menu.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])'),
      );
      if (entries.length === 0) return;
      const index = entries.indexOf(document.activeElement as HTMLElement);
      const next =
        event.key === "ArrowDown"
          ? entries[(index + 1) % entries.length]
          : entries[(index - 1 + entries.length) % entries.length];
      next?.focus();
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function close() {
    setOpen(false);
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((value) => !value)}
        className={buttonClasses({ variant: triggerVariant, size: "md", iconOnly }, triggerClassName)}
      >
        {trigger}
      </button>

      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={label}
          className={cx(
            "menu absolute z-50 mt-2 rounded-xl border border-line bg-surface p-1.5 shadow-lg",
            width,
            align === "end" ? "right-0 origin-top-right" : "left-0 origin-top-left",
          )}
        >
          {header && <div className="border-b border-line px-2.5 pt-1.5 pb-2.5 mb-1.5">{header}</div>}
          {items.map((item, index) => {
            const className = cx(
              "menu-item",
              item.tone === "danger" && "text-danger hover:text-danger",
              item.disabled && "pointer-events-none opacity-60",
            );
            const content = (
              <>
                {item.icon && <span className="shrink-0 text-muted">{item.icon}</span>}
                <span className="min-w-0 flex-1">
                  <span className="block">{item.label}</span>
                  {item.description && (
                    <span className="mt-0.5 block text-xs leading-snug text-muted">
                      {item.description}
                    </span>
                  )}
                </span>
              </>
            );
            if (item.href && !item.disabled) {
              return (
                <Link key={index} href={item.href} role="menuitem" className={className} onClick={close}>
                  {content}
                </Link>
              );
            }
            return (
              <button
                key={index}
                type="button"
                role="menuitem"
                aria-disabled={item.disabled || undefined}
                className={className}
                onClick={() => {
                  if (item.disabled) return;
                  close();
                  item.onSelect?.();
                }}
              >
                {content}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
