import type { ReactNode } from "react";

import { cx } from "@/components/ui/cx";

export function InfoHero({
  eyebrow,
  title,
  intro,
}: {
  eyebrow: string;
  title: string;
  intro: ReactNode;
}) {
  return (
    <header className="relative overflow-hidden border-b border-line">
      <div aria-hidden className="grid-backdrop pointer-events-none absolute inset-0" />
      <div className="container-page relative max-w-3xl py-16 sm:py-20">
        <p className="eyebrow">{eyebrow}</p>
        <h1 className="heading-xl mt-3">{title}</h1>
        <div className="lead mt-5">{intro}</div>
      </div>
    </header>
  );
}

export function InfoBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("container-page max-w-3xl space-y-14 py-14 sm:py-16", className)}>{children}</div>;
}

export function InfoSection({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="heading-lg">
        {title}
      </h2>
      <div className="mt-4 space-y-4 text-[0.9375rem] leading-relaxed text-ink-2">{children}</div>
    </section>
  );
}

/** A definition-style list for "what we store" and "how it works" summaries. */
export function FactList({ items }: { items: ReadonlyArray<{ term: string; detail: ReactNode }> }) {
  return (
    <dl className="card divide-y divide-line">
      {items.map((item) => (
        <div key={item.term} className="grid gap-1 px-5 py-4 sm:grid-cols-[12rem_1fr] sm:gap-6">
          <dt className="text-sm font-semibold text-ink">{item.term}</dt>
          <dd className="text-sm leading-relaxed text-muted">{item.detail}</dd>
        </div>
      ))}
    </dl>
  );
}
