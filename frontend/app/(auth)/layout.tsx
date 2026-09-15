import Link from "next/link";

import { ThemeToggle } from "@/app/ThemeToggle";
import { AuthAside } from "@/components/auth/AuthAside";
import { Logo } from "@/components/ui/Logo";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <div className="flex min-w-0 flex-col px-4 py-5 sm:px-8 lg:px-12">
        <header className="flex items-center justify-between gap-4">
          <Logo />
          <ThemeToggle />
        </header>
        <main id="main" className="flex flex-1 items-center justify-center py-12">
          <div className="w-full max-w-sm">{children}</div>
        </main>
        <footer className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted">
          <Link href="/security" className="hover:text-ink">
            Security
          </Link>
          <Link href="/privacy" className="hover:text-ink">
            Privacy
          </Link>
          <Link href="/help" className="hover:text-ink">
            Help center
          </Link>
        </footer>
      </div>
      <AuthAside />
    </div>
  );
}
