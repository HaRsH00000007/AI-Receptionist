import type { ReactNode } from "react";

import type { Tone } from "@/lib/tone";

import { cx } from "./cx";

export function Badge({
  tone = "neutral",
  dot = false,
  pulse = false,
  className,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  /** A slow ping on the dot, for something that is live right now. */
  pulse?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={cx("badge", `badge-${tone}`, className)}>
      {dot && <span className={cx("badge-dot", pulse && "badge-dot-pulse")} aria-hidden />}
      {children}
    </span>
  );
}
