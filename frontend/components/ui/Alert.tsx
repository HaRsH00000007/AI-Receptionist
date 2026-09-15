import type { ReactNode } from "react";

import { cx } from "./cx";
import { AlertTriangleIcon, CheckCircleIcon, InfoIcon, XCircleIcon } from "./icons";

type AlertTone = "info" | "success" | "warn" | "danger";

const ICONS = {
  info: InfoIcon,
  success: CheckCircleIcon,
  warn: AlertTriangleIcon,
  danger: XCircleIcon,
} as const;

/**
 * An inline message.
 *
 * Only `danger` is announced as an `alert`: interrupting a screen reader is
 * for things that went wrong. Pass `live` to announce a non-error politely.
 */
export function Alert({
  tone = "info",
  title,
  children,
  action,
  live = false,
  className,
}: {
  tone?: AlertTone;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  live?: boolean;
  className?: string;
}) {
  const Icon = ICONS[tone];
  const role = tone === "danger" ? "alert" : live ? "status" : undefined;
  return (
    <div role={role} className={cx("alert", `alert-${tone}`, className)}>
      <Icon className="mt-0.5 shrink-0" size={17} />
      <div className="min-w-0 flex-1">
        {title && <p className="alert-title">{title}</p>}
        {children && <div className="alert-body">{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}
