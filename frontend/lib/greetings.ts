/**
 * Opening lines.
 *
 * The presets below are the same three sentences the backend's deterministic
 * template writes (`_GREETING_TEMPLATES` in `app/services/config_generator.py`),
 * so a line previewed here is the line a caller actually hears. Keep the two in
 * step: a difference would quietly turn the preview into a lie.
 */

import type { GreetingStyle } from "./types";

/**
 * Matches `custom_greeting` on the signup schema, which in turn matches the
 * generated config's greeting field. Longer text is refused server-side.
 */
export const GREETING_MAX_LENGTH = 300;

const PRESETS: Record<GreetingStyle, (businessName: string) => string> = {
  professional: (name) => `Thank you for calling ${name}. How can I help you today?`,
  friendly: (name) => `Hi, thanks for calling ${name}! What can I do for you?`,
  formal: (name) => `Good day, you have reached ${name}. How may I assist you?`,
};

/** The ready-made opening line for a style, with the business's own name in it. */
export function greetingPreset(style: GreetingStyle, businessName: string): string {
  const name = businessName.trim() || "your business";
  return PRESETS[style](name);
}

/**
 * How the owner decided on their opening line.
 *
 * `auto` sends nothing and lets setup write one; the other two send the exact
 * text, so what is shown is what is spoken.
 */
export type GreetingChoice = "auto" | "preset" | "custom";

// ---------------------------------------------------------------------------
// Scheduled greetings
// ---------------------------------------------------------------------------

/**
 * Stored as minutes since local midnight, shown on a clock, typed in an
 * `<input type="time">`. Three representations, converted in one place: the
 * arithmetic is trivial and getting it wrong in two components separately is
 * how a lunch hour ends up an hour out in one of them.
 */
export function minutesToTimeValue(minute: number): string {
  const hours = Math.floor(minute / 60);
  const minutes = minute % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

/** `"12:30"` to 750. Null for anything that is not a time, which the form refuses. */
export function timeValueToMinutes(value: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
}

/** The clock as a person reads it: "12:30 PM". */
export function formatMinuteOfDay(minute: number): string {
  const hours = Math.floor(minute / 60);
  const minutes = minute % 60;
  const suffix = hours < 12 ? "AM" : "PM";
  const shown = hours % 12 === 0 ? 12 : hours % 12;
  return `${shown}:${String(minutes).padStart(2, "0")} ${suffix}`;
}

/** Monday is 0, matching the server and `Date.prototype.getDay() - 1`. */
export const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

/**
 * "Every day", "Mon–Fri", or a list.
 *
 * The two named cases are the ones businesses actually mean, and spelling out
 * seven abbreviations for "every day" reads like a bug.
 */
export function formatWeekdays(weekdays: number[]): string {
  if (weekdays.length === 0 || weekdays.length === 7) return "Every day";
  const sorted = [...weekdays].sort((a, b) => a - b);
  const consecutive = sorted.every((day, index) => index === 0 || day === sorted[index - 1]! + 1);
  if (consecutive && sorted.length > 2) {
    return `${WEEKDAY_NAMES[sorted[0]!]}–${WEEKDAY_NAMES[sorted[sorted.length - 1]!]}`;
  }
  return sorted.map((day) => WEEKDAY_NAMES[day]).join(", ");
}

/**
 * A stored `YYYY-MM-DD` as a date a person reads.
 *
 * Parsed by hand rather than with `new Date(value)`: that reads a bare date as
 * UTC midnight and then prints it in the viewer's zone, which shows the 24th as
 * the 23rd for everyone west of Greenwich.
 */
export function formatIsoDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** One date, or a range. */
export function formatDateRange(start: string | null, end: string | null): string {
  if (!start) return "";
  if (!end || end === start) return formatIsoDate(start);
  return `${formatIsoDate(start)} – ${formatIsoDate(end)}`;
}
