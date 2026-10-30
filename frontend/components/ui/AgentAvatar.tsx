/**
 * A face for a voice.
 *
 * Drawn here rather than loaded: an SVG costs no request, cannot 404, stays
 * sharp at any size and follows the theme into dark mode. Eight PNGs would need
 * an asset pipeline and would look like somebody else's product the first time
 * we changed the accent colour.
 *
 * **Why three tones and not one.** The first attempt was a one-colour
 * silhouette with the face knocked out of it, and at 40px it read as a blob:
 * nothing separated the head from the hair or from the disc behind it. A face
 * needs its own value between the hair above it and the clothing below it, so
 * each avatar is built from a skin tone, a hair colour and one theme hue for
 * the clothes — the hue is what keeps the set inside our palette, and the other
 * two are what make it a person.
 *
 * **Skin and hair are theme-independent.** A face does not invert in dark mode.
 * Only the disc and the clothing are mixed against `--surface`.
 *
 * **Which avatar belongs to which voice is the server's answer** (see
 * `app/services/avatars.py`): it holds the vendor's labels for a voice, so it
 * picks a face that matches the voice a caller will actually hear, and the
 * client just draws the key it is given.
 */

import { cx } from "./cx";

export const AGENT_AVATARS = [
  "ada",
  "iris",
  "nora",
  "vera",
  "edith",
  "leo",
  "omar",
  "theo",
  "raj",
  "walt",
] as const;

export type AgentAvatar = (typeof AGENT_AVATARS)[number];

function isAvatar(value: string | null | undefined): value is AgentAvatar {
  return !!value && (AGENT_AVATARS as readonly string[]).includes(value);
}

/**
 * A fallback for anything the server did not label.
 *
 * Derived from a stable seed rather than picked at random, so the same voice or
 * business is the same face on every device and after every reload — a face
 * that changed on refresh would read as a bug.
 */
export function avatarFor(stored: string | null | undefined, seed: string): AgentAvatar {
  if (isAvatar(stored)) return stored;
  let hash = 0;
  for (const character of seed) hash = (hash * 31 + character.charCodeAt(0)) % 100_000;
  return AGENT_AVATARS[hash % AGENT_AVATARS.length]!;
}

/** A small, deliberately unremarkable range. Fixed across themes. */
const SKIN = {
  light: "#f1c9a5",
  tan: "#d79f72",
  brown: "#a9703f",
  deep: "#774625",
} as const;

const HAIR = {
  black: "#2b2724",
  brown: "#5a3b22",
  auburn: "#8c4a24",
  blonde: "#c98f3f",
  grey: "#9aa0a6",
} as const;

interface Face {
  /** The theme hue the clothing and the disc are drawn from. */
  hue: string;
  skin: (typeof SKIN)[keyof typeof SKIN];
  hair: (typeof HAIR)[keyof typeof HAIR];
  /** Drawn before the shoulders, so long hair falls behind them. */
  behind?: string;
  /** Drawn over the head. */
  over: string;
  beard?: string;
  glasses?: boolean;
}

/**
 * The cast.
 *
 * Ten rather than eight so that two voices the vendor labels the same way — two
 * young American women, say — do not have to be the same face. The server
 * spreads them within a bucket.
 */
const FACES: Record<AgentAvatar, Face> = {
  // Long straight hair, falling behind the shoulders.
  ada: {
    hue: "indigo",
    skin: SKIN.light,
    hair: HAIR.brown,
    behind:
      "M16 26c0-12 7-19 16-19s16 7 16 19v18a4.5 4.5 0 0 1-9 0V26H25v18a4.5 4.5 0 0 1-9 0z",
    over: "M32 11c-9 0-14 6-14 14v3c3-1 6-4 7-7 3 4 10 6 15 5 2 1 4 2 6 2v-3c0-8-5-14-14-14z",
  },
  // Round curly volume.
  iris: {
    hue: "violet",
    skin: SKIN.deep,
    hair: HAIR.black,
    over: "M32 7c-11 0-17 8-17 17 0 4 1 7 2 9 1-5 2-9 4-11 1 4 4 6 11 6s10-2 11-6c2 2 3 6 4 11 1-2 2-5 2-9 0-9-6-17-17-17z",
  },
  // Chin-length bob.
  nora: {
    hue: "teal",
    skin: SKIN.light,
    hair: HAIR.black,
    behind:
      "M17 26c0-11 7-17 15-17s15 6 15 17v11a4.5 4.5 0 0 1-9 0V26H26v11a4.5 4.5 0 0 1-9 0z",
    over: "M32 11c-9 0-14 6-14 14v2c4-1 7-4 8-7 3 4 9 6 14 5 2 1 4 2 6 2v-2c0-8-5-14-14-14z",
  },
  // Hair gathered into a bun.
  vera: {
    hue: "rose",
    skin: SKIN.tan,
    hair: HAIR.brown,
    over: "M32 5a4.5 4.5 0 0 0-2 8.5c-7 1-12 6-12 13v2c4-1 7-4 8-7 3 4 9 6 14 5 2 1 4 2 6 2v-2c0-7-5-12-12-13A4.5 4.5 0 0 0 32 5z",
  },
  // Short, grey, with glasses.
  edith: {
    hue: "slate",
    skin: SKIN.light,
    hair: HAIR.grey,
    over: "M32 10c-9 0-14 6-14 14v3c2-1 4-3 5-5 4 3 14 3 18 0 1 2 3 4 5 5v-3c0-8-5-14-14-14z",
    glasses: true,
  },
  // Short crop.
  leo: {
    hue: "azure",
    skin: SKIN.tan,
    hair: HAIR.brown,
    over: "M32 10c-9 0-14 6-14 14v2c2-1 4-3 5-5 4 3 14 3 18 0 1 2 3 4 5 5v-2c0-8-5-14-14-14z",
  },
  // Close-cropped.
  omar: {
    hue: "moss",
    skin: SKIN.brown,
    hair: HAIR.black,
    over: "M32 10c-9 0-14 5-14 13v1c3-1 5-3 6-5 4 3 12 3 16 0 1 2 3 4 6 5v-1c0-8-5-13-14-13z",
  },
  // Short hair and a full beard.
  theo: {
    hue: "amber",
    skin: SKIN.light,
    hair: HAIR.auburn,
    over: "M32 10c-9 0-14 6-14 14v2c2-1 4-3 5-5 4 3 14 3 18 0 1 2 3 4 5 5v-2c0-8-5-14-14-14z",
    beard: "M21 26c0 10 5 17 11 17s11-7 11-17c-1 4-3 6-4 7-2 2-4 3-7 3s-5-1-7-3c-1-1-3-3-4-7z",
  },
  // Side-parted, with glasses.
  raj: {
    hue: "indigo",
    skin: SKIN.brown,
    hair: HAIR.black,
    over: "M32 10c-9 0-14 6-14 14v2c2-1 4-3 5-5 4 3 14 3 18 0 1 2 3 4 5 5v-2c0-8-5-14-14-14z",
    glasses: true,
  },
  // Thinning grey, with a beard.
  walt: {
    hue: "teal",
    skin: SKIN.light,
    hair: HAIR.grey,
    over: "M32 11c-8 0-13 5-14 12 2-2 4-4 6-5 4 2 12 2 16 0 2 1 4 3 6 5-1-7-6-12-14-12z",
    beard: "M22 27c0 9 4 16 10 16s10-7 10-16c-1 3-2 5-3 6-2 2-4 3-7 3s-5-1-7-3c-1-1-2-3-3-6z",
  },
};

/** Shared, so every face looks out of the same place. */
const HEAD = { cx: 32, cy: 26, rx: 11, ry: 12.5 };
const INK = "#2b2724";

export function AgentAvatarImage({
  avatar,
  size = 40,
  className,
  title,
}: {
  avatar: AgentAvatar;
  size?: number;
  className?: string;
  /** An accessible name. Omit for an avatar sitting beside its own label. */
  title?: string;
}) {
  const face = FACES[avatar];
  const hue = `var(--avatar-${face.hue})`;
  const disc = `color-mix(in oklab, ${hue} 14%, var(--surface))`;
  const clip = `avatar-clip-${avatar}`;

  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={cx("shrink-0 rounded-full", className)}
      role={title ? "img" : "presentation"}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      {/* The id is per avatar and the contents are identical wherever it is
          reused, so two of the same face on one page share a clip harmlessly. */}
      <clipPath id={clip}>
        <circle cx="32" cy="32" r="32" />
      </clipPath>
      <circle cx="32" cy="32" r="32" fill={disc} />
      <g clipPath={`url(#${clip})`}>
        {face.behind ? <path d={face.behind} fill={face.hair} /> : null}
        {/* Shoulders, then neck, then head: each covers the join below it. */}
        <path d="M32 41c-12 0-21 9-21 23h42c0-14-9-23-21-23z" fill={hue} />
        <rect x="28" y="33" width="8" height="10" rx="4" fill={face.skin} />
        <ellipse cx="21.8" cy="27.5" rx="2.2" ry="2.8" fill={face.skin} />
        <ellipse cx="42.2" cy="27.5" rx="2.2" ry="2.8" fill={face.skin} />
        <ellipse {...HEAD} fill={face.skin} />
        <path d={face.over} fill={face.hair} />
        {face.beard ? <path d={face.beard} fill={face.hair} /> : null}
        {face.glasses ? (
          <g fill="none" stroke={INK} strokeWidth="1.3">
            <circle cx="27.4" cy="26.4" r="3.6" />
            <circle cx="36.6" cy="26.4" r="3.6" />
            <path d="M31 26.4h2" />
          </g>
        ) : (
          <>
            <circle cx="27.6" cy="26" r="1.5" fill={INK} />
            <circle cx="36.4" cy="26" r="1.5" fill={INK} />
          </>
        )}
        <path
          d="M28.6 31.8a4.6 4.6 0 0 0 6.8 0"
          fill="none"
          stroke={INK}
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </g>
    </svg>
  );
}
