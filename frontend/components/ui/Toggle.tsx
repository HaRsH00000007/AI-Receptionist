"use client";

/**
 * A switch.
 *
 * A real checkbox underneath, so it is keyboard- and screen-reader-operable and
 * announces its own state; everything visible is the styled track and knob.
 * `disabled` is honoured rather than hidden, because a member who cannot change
 * a setting should still be able to see what it is.
 *
 * Lifted out of the website wizard, which had the only copy, when the tags
 * panel needed the same control. The markup is unchanged.
 */

import { CheckIcon } from "./icons";
import { cx } from "./cx";

export function Toggle({
  label,
  on,
  disabled,
  onToggle,
}: {
  /** The accessible name. The switch itself carries no visible text. */
  label: string;
  on: boolean;
  disabled?: boolean;
  onToggle: (on: boolean) => void;
}) {
  return (
    <label
      className={cx(
        "relative inline-flex shrink-0 items-center",
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
      )}
    >
      <input
        type="checkbox"
        className="peer sr-only"
        checked={on}
        disabled={disabled}
        aria-label={label}
        onChange={(event) => onToggle(event.target.checked)}
      />
      <span
        className={cx(
          "flex h-6 w-10 items-center rounded-full px-0.5 transition-colors",
          "peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent",
          on ? "bg-accent" : "bg-line",
        )}
      >
        <span
          className={cx(
            "flex size-5 items-center justify-center rounded-full bg-surface transition-transform",
            on && "translate-x-4 text-accent",
          )}
        >
          {on && <CheckIcon size={12} />}
        </span>
      </span>
    </label>
  );
}
