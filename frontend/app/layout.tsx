import type { Metadata } from "next";
import Link from "next/link";

import { ThemeToggle } from "./ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Receptionist",
  description: "Give your business a phone number answered by an AI receptionist.",
};

/**
 * Runs before the page paints, so the correct palette is in place on the very
 * first frame. Without it every dark-mode visitor gets a white flash.
 * Deliberately tiny and dependency-free — it is inlined into the HTML.
 */
const THEME_SCRIPT = `
try {
  var stored = localStorage.getItem('ai-receptionist-theme');
  var dark = stored ? stored === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
} catch (e) {}
`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // The script below sets `data-theme` before React hydrates, which is a
    // deliberate mismatch with what the server rendered.
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen antialiased">
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />

        <header className="sticky top-0 z-10 border-b border-line bg-surface/80 backdrop-blur-md">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-6 py-3.5">
            <Link
              href="/"
              className="group flex items-center gap-2.5 rounded-lg outline-none focus-visible:shadow-[var(--ring)]"
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-accent-fg">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden
                >
                  <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2 4.2 2 2 0 0 1 4 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.1a2 2 0 0 1 2.1-.5c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z" />
                </svg>
              </span>
              <span className="text-sm font-semibold tracking-tight">AI Receptionist</span>
            </Link>

            <div className="flex items-center gap-2">
              <span className="hidden rounded-full border border-line px-2.5 py-1 text-[11px] font-medium text-muted sm:inline">
                POC
              </span>
              <ThemeToggle />
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-3xl px-6 py-10 sm:py-14">{children}</main>

        <footer className="mx-auto max-w-3xl px-6 pb-10 text-xs text-muted">
          Provisioning runs in the background — you can leave this page and come back.
        </footer>
      </body>
    </html>
  );
}
