/**
 * Display formatting.
 *
 * Every value the API returns in a machine shape — E.164 numbers, ISO
 * timestamps, snake_case codes, second counts — is turned into words here, so
 * no page invents its own slightly different version of "1m 35s".
 */

const NANP = /^\+1(\d{3})(\d{3})(\d{4})$/;

/** `+18055550100` → `+1 (805) 555-0100`. Anything not North American is left as-is. */
export function formatPhone(value: string | null | undefined): string {
  if (!value) return "—";
  const match = NANP.exec(value.trim());
  return match ? `+1 (${match[1]}) ${match[2]}-${match[3]}` : value;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  if (minutes === 0) return `${remainder}s`;
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function toDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(
  iso: string | null | undefined,
  options: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" },
): string {
  const date = toDate(iso);
  return date ? new Intl.DateTimeFormat(undefined, options).format(date) : "—";
}

export function formatDate(iso: string | null | undefined): string {
  return formatDateTime(iso, { dateStyle: "medium" });
}

export function formatTime(iso: string | null | undefined): string {
  return formatDateTime(iso, { timeStyle: "short" });
}

/**
 * A calendar date with no time (`2026-09-01`).
 *
 * Parsed and printed in UTC: read as a local timestamp, midnight UTC is the
 * previous evening anywhere west of Greenwich, and a billing period would
 * appear to start a day early.
 */
export function formatCalendarDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = toDate(value.length === 10 ? `${value}T00:00:00Z` : value);
  return date
    ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" }).format(date)
    : "—";
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  const date = toDate(iso);
  if (!date) return "—";
  const diff = date.getTime() - now;
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (abs < MINUTE) return "just now";
  if (abs < HOUR) return rtf.format(Math.round(diff / MINUTE), "minute");
  if (abs < DAY) return rtf.format(Math.round(diff / HOUR), "hour");
  if (abs < 7 * DAY) return rtf.format(Math.round(diff / DAY), "day");
  return formatDate(iso);
}

/** `booking_request` → `Booking request`. */
export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const words = value.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "—";
}

export function initials(value: string | null | undefined): string {
  const letters = (value ?? "")
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word.charAt(0).toUpperCase())
    .join("");
  return letters || "?";
}

/**
 * `"09:30"` → `9:30 AM`, in the viewer's locale.
 *
 * Opening hours are wall-clock times local to the business, so they are
 * formatted in UTC purely to stop the viewer's own timezone shifting them.
 */
export function formatClock(value: string | null | undefined): string {
  if (!value) return "—";
  const [hours, minutes] = value.split(":").map(Number);
  if (hours === undefined || minutes === undefined || Number.isNaN(hours) || Number.isNaN(minutes)) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: minutes ? "2-digit" : undefined,
    timeZone: "UTC",
  }).format(new Date(Date.UTC(2000, 0, 1, hours, minutes)));
}

/** `["a", "b", "c"]` → `a, b, and c`. */
export function formatList(items: readonly string[]): string {
  return new Intl.ListFormat(undefined, { style: "long", type: "conjunction" }).format(items);
}

export function formatNumber(value: number): string {
  return value.toLocaleString();
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${formatNumber(count)} ${count === 1 ? singular : plural}`;
}
