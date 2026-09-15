"use client";

/**
 * The public site's navigation.
 *
 * Transparent over the hero, then solid once the page has scrolled — so the
 * hero reads as one surface on arrival and the nav stays legible over content.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { Button, ButtonLink } from "@/components/ui/Button";
import { cx } from "@/components/ui/cx";
import { Dialog } from "@/components/ui/Dialog";
import { MenuIcon } from "@/components/ui/icons";
import { Logo } from "@/components/ui/Logo";

import { NAV_LINKS } from "./links";

export function MarketingNav() {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    function onScroll() {
      setScrolled(window.scrollY > 8);
    }
    // Deferred a frame: a page restored mid-scroll should start solid, but the
    // first read belongs in a callback rather than the effect body.
    const frame = requestAnimationFrame(onScroll);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  const close = () => setMenuOpen(false);

  return (
    <header
      className={cx(
        "sticky top-0 z-50 border-b transition-[background-color,border-color] duration-200",
        scrolled ? "border-line bg-bg/85 backdrop-blur-md" : "border-transparent bg-transparent",
      )}
    >
      <nav aria-label="Main" className="container-page flex h-16 items-center justify-between gap-4">
        <Logo />

        <ul className="hidden items-center gap-1 lg:flex">
          {NAV_LINKS.map((link) => (
            <li key={link.href}>
              <Link
                href={link.href}
                className="rounded-lg px-3 py-2 text-sm font-medium text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
              >
                {link.label}
              </Link>
            </li>
          ))}
        </ul>

        <div className="flex items-center gap-2">
          <ButtonLink href="/login" variant="ghost" className="hidden text-ink-2 sm:inline-flex">
            Log in
          </ButtonLink>
          <ButtonLink href="/get-started" className="hidden sm:inline-flex">
            Get started
          </ButtonLink>
          <Button
            variant="ghost"
            iconOnly
            className="text-ink lg:hidden"
            aria-label="Open menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
          >
            <MenuIcon size={20} />
          </Button>
        </div>
      </nav>

      <Dialog open={menuOpen} onClose={close} variant="sheet" title="Menu">
        <ul className="-mx-2 space-y-1">
          {NAV_LINKS.map((link) => (
            <li key={link.href}>
              <Link
                href={link.href}
                onClick={close}
                className="block rounded-lg px-3 py-2.5 text-[0.9375rem] font-medium text-ink-2 hover:bg-surface-2 hover:text-ink"
              >
                {link.label}
              </Link>
            </li>
          ))}
        </ul>
        <div className="mt-6 flex flex-col gap-2 border-t border-line pt-6">
          <ButtonLink href="/get-started" size="lg" block onClick={close}>
            Get started
          </ButtonLink>
          <ButtonLink href="/login" variant="secondary" size="lg" block onClick={close}>
            Log in
          </ButtonLink>
        </div>
      </Dialog>
    </header>
  );
}
