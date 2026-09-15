import type { ComponentProps, ReactNode } from "react";

import { cx } from "./cx";

export function Card({
  className,
  interactive = false,
  ...rest
}: ComponentProps<"div"> & { interactive?: boolean }) {
  return <div className={cx("card", interactive && "card-interactive", className)} {...rest} />;
}

export function CardHeader({
  title,
  description,
  action,
  className,
  as: Heading = "h2",
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  as?: "h2" | "h3";
}) {
  return (
    <div
      className={cx(
        "flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-line px-5 py-4",
        className,
      )}
    >
      <div className="min-w-0">
        <Heading className="text-[0.9375rem] font-semibold tracking-tight">{title}</Heading>
        {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  );
}
