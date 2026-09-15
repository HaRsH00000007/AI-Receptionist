"use client";

import { useEffect, useState } from "react";

import { Button, type ButtonSize, type ButtonVariant } from "./Button";
import { CheckIcon, CopyIcon } from "./icons";

export function CopyButton({
  value,
  label = "Copy",
  size = "sm",
  variant = "secondary",
  className,
}: {
  value: string;
  label?: string;
  size?: ButtonSize;
  variant?: ButtonVariant;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1_800);
    return () => clearTimeout(timer);
  }, [copied]);

  return (
    <Button
      size={size}
      variant={variant}
      className={className}
      leadingIcon={copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
        } catch {
          // Clipboard access was refused. The value is still on screen to copy by hand.
        }
      }}
    >
      <span aria-live="polite">{copied ? "Copied" : label}</span>
    </Button>
  );
}
