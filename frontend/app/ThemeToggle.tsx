"use client";

/**
 * Light/dark switch.
 *
 * The choice is stored per browser in `localStorage`; with nothing stored the
 * page follows the operating system. Stamping it onto `<html data-theme>`
 * happens in a blocking script in the layout, so this component only has to
 * keep that attribute in step once React is running.
 *
 * The theme is read through `useSyncExternalStore` rather than an effect,
 * because it *is* external state: it lives in the browser, it can change
 * without React (another tab, or the OS switching at sunset), and the server
 * cannot know it. The store below is what makes those three sources one value.
 */

import { useSyncExternalStore } from "react";

type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "ai-receptionist-theme";

const listeners = new Set<() => void>();

function notify() {
  for (const listener of listeners) listener();
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  // `storage` fires in *other* tabs; `media` fires when the OS flips. A change
  // made in this tab is announced by `notify()` in setTheme.
  media.addEventListener("change", onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    media.removeEventListener("change", onChange);
    window.removeEventListener("storage", onChange);
  };
}

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    // Private browsing, or storage disabled. Falling through to the system
    // preference is correct; a broken toggle would not be.
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** What the server renders. The real value is only knowable in the browser. */
function serverTheme(): Theme {
  return "light";
}

function setTheme(next: Theme) {
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch {
    // The theme still applies to this page; it just will not be remembered.
  }
  notify();
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, readTheme, serverTheme);
  const label = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";

  return (
    <button
      type="button"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      aria-label={label}
      title={label}
      className="btn btn-ghost h-9 w-9 !p-0"
    >
      {/* The first client render may disagree with the server's guess; that is
          the point of the blocking script, and not worth a warning. */}
      <span aria-hidden suppressHydrationWarning>
        {theme === "dark" ? <SunIcon /> : <MoonIcon />}
      </span>
    </button>
  );
}

function SunIcon() {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  );
}
