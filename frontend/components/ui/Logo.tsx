import Link from "next/link";

import { BRAND } from "@/lib/brand";

import { cx } from "./cx";

/** The mark: a steady line that lifts into a voice pulse. */
export function LogoMark({ size = 28, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className={cx("shrink-0", className)}>
      <rect width="32" height="32" rx="9" fill="var(--accent)" />
      <path
        d="M6.5 16.5h3.4l2.4-5.5 3.8 11 2.6-7.6 1.7 2.1h5.1"
        fill="none"
        stroke="var(--accent-fg)"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Logo({
  href = "/",
  className,
  inverse = false,
}: {
  href?: string;
  className?: string;
  /** For dark bands, where the wordmark must stay light in either theme. */
  inverse?: boolean;
}) {
  return (
    <Link
      href={href}
      className={cx(
        "inline-flex items-center gap-2.5 rounded-lg text-[1.0625rem] font-bold tracking-tight",
        inverse ? "text-night-ink" : "text-ink",
        className,
      )}
    >
      <LogoMark />
      <span>{BRAND.name}</span>
    </Link>
  );
}
