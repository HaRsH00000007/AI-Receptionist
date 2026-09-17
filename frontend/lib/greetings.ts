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
