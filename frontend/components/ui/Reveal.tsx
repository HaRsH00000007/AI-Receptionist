"use client";

/**
 * Fades its content up the first time it scrolls into view.
 *
 * The visible state is a class set directly on the node rather than React
 * state: it changes once, never needs to re-render anything, and keeping it out
 * of state keeps setState out of the effect. Content is hidden only when the
 * root layout has marked the document as scripted, and reduced-motion users get
 * it immediately (see `globals.css`).
 */

import { useEffect, useRef, type ReactNode } from "react";

import { cx } from "./cx";

export function Reveal({
  children,
  className,
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  /** Milliseconds, for staggering siblings. */
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      node.classList.add("is-visible");
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          node.classList.add("is-visible");
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.1 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={cx("reveal", className)}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  );
}
