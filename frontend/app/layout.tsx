import type { Metadata, Viewport } from "next";

import "@fontsource-variable/plus-jakarta-sans";
import "./globals.css";

import { ToastProvider } from "@/components/ui/Toast";
import { BRAND } from "@/lib/brand";

export const metadata: Metadata = {
  title: { default: `${BRAND.name} · ${BRAND.tagline}`, template: `%s · ${BRAND.name}` },
  description: BRAND.description,
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f7f4" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0c11" },
  ],
};

/**
 * Runs before the page paints, so the correct palette is in place on the very
 * first frame — without it every dark-mode visitor gets a white flash. It also
 * marks the document as scripted, which is what lets scroll-reveal content
 * start hidden without disappearing for anyone running without JavaScript.
 */
const THEME_SCRIPT = `
try {
  document.documentElement.classList.add('js');
  var stored = localStorage.getItem('ai-receptionist-theme');
  var dark = stored ? stored === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
} catch (e) {}
`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    // The script below sets `data-theme` before React hydrates, which is a
    // deliberate mismatch with what the server rendered.
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-dvh">
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
