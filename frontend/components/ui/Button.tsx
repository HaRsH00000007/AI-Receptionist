import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

import { cx } from "./cx";
import { Spinner } from "./Spinner";

export type ButtonVariant =
  | "primary"
  | "secondary"
  | "ghost"
  | "danger"
  | "inverse"
  | "inverse-ghost";
export type ButtonSize = "sm" | "md" | "lg";

interface StyleProps {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Square, for a control whose only content is an icon. Label it. */
  iconOnly?: boolean;
  block?: boolean;
}

export function buttonClasses(
  { variant = "primary", size = "md", iconOnly = false, block = false }: StyleProps,
  className?: string,
): string {
  return cx("btn", `btn-${variant}`, `btn-${size}`, iconOnly && "btn-icon", block && "w-full", className);
}

interface ButtonProps extends ComponentProps<"button">, StyleProps {
  loading?: boolean;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
}

export function Button({
  variant,
  size,
  iconOnly,
  block,
  loading = false,
  leadingIcon,
  trailingIcon,
  className,
  children,
  disabled,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={buttonClasses({ variant, size, iconOnly, block }, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Spinner className="size-4" /> : leadingIcon}
      {children}
      {trailingIcon}
    </button>
  );
}

interface ButtonLinkProps extends ComponentProps<typeof Link>, StyleProps {
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
}

export function ButtonLink({
  variant,
  size,
  iconOnly,
  block,
  leadingIcon,
  trailingIcon,
  className,
  children,
  ...rest
}: ButtonLinkProps) {
  return (
    <Link className={buttonClasses({ variant, size, iconOnly, block }, className)} {...rest}>
      {leadingIcon}
      {children}
      {trailingIcon}
    </Link>
  );
}
