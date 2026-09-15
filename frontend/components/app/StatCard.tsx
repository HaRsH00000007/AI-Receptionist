import Link from "next/link";
import type { ReactNode } from "react";

import { cx } from "@/components/ui/cx";

export function StatCard({
  label,
  value,
  hint,
  icon,
  href,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  href?: string;
}) {
  const body = (
    <>
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-muted">{label}</p>
        {icon && (
          <span className="flex size-8 items-center justify-center rounded-lg bg-surface-2 text-ink-2">
            {icon}
          </span>
        )}
      </div>
      <p className="mt-3 text-[1.75rem] leading-none font-bold tracking-tight tabular-nums">{value}</p>
      {hint && <p className="mt-2 text-sm text-muted">{hint}</p>}
    </>
  );

  if (href) {
    return (
      <Link href={href} className={cx("card card-interactive block p-5")}>
        {body}
      </Link>
    );
  }
  return <div className="card p-5">{body}</div>;
}
