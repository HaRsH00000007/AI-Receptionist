"use client";

/**
 * Labelled form controls.
 *
 * Each control owns its label, hint and error wiring: `htmlFor`/`id`,
 * `aria-describedby` and `aria-invalid` are derived here, so no form can
 * forget them and every field is reachable by its visible label.
 */

import { useId, type ComponentProps, type ReactNode } from "react";

import { cx } from "./cx";

interface ShellProps {
  label: string;
  hint?: ReactNode;
  error?: string;
  optional?: boolean;
  className?: string;
}

function useFieldIds(error?: string, hint?: ReactNode) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return { id, hintId, errorId, describedBy };
}

function Shell({
  controlId,
  label,
  hint,
  hintId,
  error,
  errorId,
  optional,
  className,
  children,
}: ShellProps & {
  controlId: string;
  hintId?: string;
  errorId?: string;
  children: ReactNode;
}) {
  return (
    <div className={className}>
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={controlId} className="field-label">
          {label}
        </label>
        {optional && <span className="text-xs text-subtle">Optional</span>}
      </div>
      {children}
      {hint && (
        <p id={hintId} className="field-hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="field-error">
          {error}
        </p>
      )}
    </div>
  );
}

type TextFieldProps = ShellProps & Omit<ComponentProps<"input">, "className">;

export function TextField({ label, hint, error, optional, className, id, ...input }: TextFieldProps) {
  const ids = useFieldIds(error, hint);
  const controlId = id ?? ids.id;
  return (
    <Shell
      controlId={controlId}
      label={label}
      hint={hint}
      hintId={ids.hintId}
      error={error}
      errorId={ids.errorId}
      optional={optional}
      className={className}
    >
      <input
        id={controlId}
        className={cx("field", error && "field-invalid")}
        aria-invalid={error ? true : undefined}
        aria-describedby={ids.describedBy}
        {...input}
      />
    </Shell>
  );
}

type TextAreaFieldProps = ShellProps & Omit<ComponentProps<"textarea">, "className">;

export function TextAreaField({
  label,
  hint,
  error,
  optional,
  className,
  id,
  ...textarea
}: TextAreaFieldProps) {
  const ids = useFieldIds(error, hint);
  const controlId = id ?? ids.id;
  return (
    <Shell
      controlId={controlId}
      label={label}
      hint={hint}
      hintId={ids.hintId}
      error={error}
      errorId={ids.errorId}
      optional={optional}
      className={className}
    >
      <textarea
        id={controlId}
        className={cx("field", error && "field-invalid")}
        aria-invalid={error ? true : undefined}
        aria-describedby={ids.describedBy}
        {...textarea}
      />
    </Shell>
  );
}

type SelectFieldProps = ShellProps &
  Omit<ComponentProps<"select">, "className" | "children"> & {
    options: ReadonlyArray<{ value: string; label: string }>;
  };

export function SelectField({
  label,
  hint,
  error,
  optional,
  className,
  id,
  options,
  ...select
}: SelectFieldProps) {
  const ids = useFieldIds(error, hint);
  const controlId = id ?? ids.id;
  return (
    <Shell
      controlId={controlId}
      label={label}
      hint={hint}
      hintId={ids.hintId}
      error={error}
      errorId={ids.errorId}
      optional={optional}
      className={className}
    >
      <select
        id={controlId}
        className={cx("field", error && "field-invalid")}
        aria-invalid={error ? true : undefined}
        aria-describedby={ids.describedBy}
        {...select}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </Shell>
  );
}

export interface ChoiceOption<T extends string> {
  value: T;
  label: string;
  description?: ReactNode;
  icon?: ReactNode;
}

const COLUMNS = {
  1: "grid-cols-1",
  2: "grid-cols-1 sm:grid-cols-2",
  3: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
} as const;

/**
 * A radio group drawn as cards. Real radio inputs underneath, so arrow keys,
 * form semantics and screen readers all behave as they would for native ones.
 */
export function ChoiceGroup<T extends string>({
  legend,
  hideLegend = false,
  name,
  value,
  onChange,
  options,
  columns = 2,
  error,
  className,
}: {
  legend: string;
  hideLegend?: boolean;
  name: string;
  value: T;
  onChange: (value: T) => void;
  options: ReadonlyArray<ChoiceOption<T>>;
  columns?: keyof typeof COLUMNS;
  error?: string;
  className?: string;
}) {
  return (
    <fieldset className={className}>
      <legend className={cx("field-label", hideLegend && "sr-only")}>{legend}</legend>
      <div className={cx("grid gap-3", !hideLegend && "mt-2.5", COLUMNS[columns])}>
        {options.map((option) => {
          const checked = option.value === value;
          return (
            <label key={option.value} className={cx("choice", checked && "choice-checked")}>
              <input
                type="radio"
                className="sr-only"
                name={name}
                value={option.value}
                checked={checked}
                onChange={() => onChange(option.value)}
              />
              {option.icon && <span className="choice-icon">{option.icon}</span>}
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-ink">{option.label}</span>
                {option.description && (
                  <span className="mt-0.5 block text-sm leading-snug text-muted">
                    {option.description}
                  </span>
                )}
              </span>
              <span className="choice-radio" aria-hidden />
            </label>
          );
        })}
      </div>
      {error && <p className="field-error">{error}</p>}
    </fieldset>
  );
}
