"use client";

/**
 * Choosing the voice that answers the phone.
 *
 * One component, used in two places on purpose: the signup wizard's voice
 * screen and the Agent settings panel. The screen promises "you can change
 * this later", and the cheapest way to keep that promise honest is for later
 * to look exactly like now.
 *
 * **A face and a sample.** The face is drawn from the vendor's own labels for
 * the voice — its gender and its age — so the picture matches the voice a
 * caller will hear rather than being decoration (`app/services/avatars.py`
 * picks it, `AgentAvatar.tsx` draws it). The sample underneath is the thing
 * actually being chosen, and it is the vendor's recording of that voice rather
 * than anything generated here.
 *
 * **Six, then the rest.** Thirty voices is not a choice, it is a decision
 * nobody wants to make mid-signup. The server marks a recommended few; the
 * rest are one click away.
 *
 * **The samples follow the talking speed.** The control for it sits directly
 * under this grid, so a sample that ignored it would be demonstrating a setting
 * the customer is not choosing. It is applied as `playbackRate` on the vendor's
 * recording, with pitch preserved — close to what the vendor does at synthesis,
 * which is to change the speaking rate rather than resample, but not identical
 * to it. Generating a true sample per speed would be a billed API call on every
 * click of a button people press while comparing voices.
 */

import { useEffect, useRef, useState } from "react";

import { AgentAvatarImage, avatarFor } from "@/components/ui/AgentAvatar";
import { Button } from "@/components/ui/Button";
import { cx } from "@/components/ui/cx";
import { SelectField } from "@/components/ui/Field";
import { PlayIcon, StopIcon } from "@/components/ui/icons";
import type { VoiceOptionView } from "@/lib/types";

/** How a language code is shown. Only English is offered today. */
const LANGUAGE_NAMES: Record<string, string> = {
  en: "English",
};

/**
 * Play a sample at the chosen speed, with the pitch left alone.
 *
 * Without `preservesPitch` the browser resamples and the voice goes squeaky,
 * which is not what the vendor's speed setting does — it changes the speaking
 * rate. The prefixed properties are for older WebKit, which shipped this
 * behind a vendor name and defaults it the wrong way.
 */
function applySpeed(element: HTMLAudioElement, speed: number) {
  const audio = element as HTMLAudioElement & {
    mozPreservesPitch?: boolean;
    webkitPreservesPitch?: boolean;
  };
  audio.preservesPitch = true;
  audio.mozPreservesPitch = true;
  audio.webkitPreservesPitch = true;
  // Guarded: a browser refuses a rate outside its supported range by throwing,
  // and a sample at the wrong speed is better than a picker that stops working.
  try {
    element.playbackRate = speed;
  } catch {
    element.playbackRate = 1;
  }
}

export function VoicePicker({
  voices,
  voiceId,
  speed = 1,
  disabled,
  onPick,
}: {
  voices: VoiceOptionView[];
  voiceId: string;
  /** The chosen talking speed, so a sample is heard at the speed being set. */
  speed?: number;
  disabled?: boolean;
  onPick: (voiceId: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);

  // One element for every sample, so starting a second one stops the first:
  // two voices talking over each other tells you nothing about either.
  useEffect(() => {
    const element = new Audio();
    element.addEventListener("ended", () => setPlaying(null));
    element.addEventListener("error", () => setPlaying(null));
    audio.current = element;
    return () => {
      element.pause();
      audio.current = null;
    };
  }, []);

  /**
   * Keep a sample that is already playing in step with the speed control.
   *
   * Changing the speed mid-sample is exactly how somebody compares two of them,
   * and restarting the clip to apply it would lose their place.
   */
  useEffect(() => {
    const element = audio.current;
    if (element) applySpeed(element, speed);
  }, [speed]);

  function toggle(voice: VoiceOptionView) {
    const element = audio.current;
    if (!element || !voice.preview_url) return;
    if (playing === voice.voice_id) {
      element.pause();
      setPlaying(null);
      return;
    }
    element.src = voice.preview_url;
    applySpeed(element, speed);
    setPlaying(voice.voice_id);
    // `play()` can be refused (autoplay policy), can reject later, and in some
    // environments returns nothing at all rather than a promise. All three end
    // the same way: the button must not stick on "Stop" for a sample that is
    // not playing.
    try {
      const started = element.play() as Promise<void> | undefined;
      if (started) void started.catch(() => setPlaying(null));
    } catch {
      setPlaying(null);
    }
  }

  const languages = [...new Set(voices.map((voice) => voice.language))].sort();
  const recommended = voices.filter((voice) => voice.recommended);
  const shown = showAll || recommended.length === 0 ? voices : recommended;
  const hidden = voices.length - shown.length;

  return (
    <div className="space-y-4">
      {languages.length > 0 ? (
        <div className="mx-auto max-w-xs">
          <SelectField
            label="Language"
            value={languages[0]}
            disabled={languages.length === 1}
            options={languages.map((code) => ({
              value: code,
              label: LANGUAGE_NAMES[code] ?? code.toUpperCase(),
            }))}
            onChange={() => undefined}
            hint={
              languages.length === 1
                ? "English is the only language your receptionist speaks today."
                : undefined
            }
          />
        </div>
      ) : null}

      <ul role="radiogroup" aria-label="Voice" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((voice) => {
          const chosen = voice.voice_id === voiceId;
          return (
            // `presentation` so the list item does not sit between the group
            // and its radios, which would break the ownership an assistive
            // technology reads the group by.
            <li key={voice.voice_id} role="presentation">
              <div
                role="radio"
                aria-checked={chosen}
                tabIndex={disabled ? -1 : 0}
                aria-disabled={disabled || undefined}
                onClick={() => !disabled && onPick(voice.voice_id)}
                onKeyDown={(event) => {
                  if (disabled) return;
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onPick(voice.voice_id);
                  }
                }}
                className={cx(
                  "flex h-full cursor-pointer flex-col items-center gap-2 rounded-xl border p-4 text-center transition-colors",
                  chosen
                    ? "border-accent bg-accent-soft"
                    : "border-line hover:border-accent/50 hover:bg-surface-2",
                  disabled && "cursor-not-allowed opacity-60",
                )}
              >
                <AgentAvatarImage avatar={avatarFor(voice.avatar, voice.voice_id)} size={56} />
                <span className="font-semibold text-ink">{voice.label}</span>
                <span className="text-sm text-muted">{voice.summary}</span>
                {voice.preview_url ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={disabled}
                    leadingIcon={
                      playing === voice.voice_id ? <StopIcon size={14} /> : <PlayIcon size={14} />
                    }
                    onClick={(event) => {
                      // The card itself selects; the button only listens.
                      event.stopPropagation();
                      toggle(voice);
                    }}
                  >
                    {playing === voice.voice_id ? "Stop" : "Hear it"}
                  </Button>
                ) : (
                  <span className="text-xs text-subtle">No sample for this one</span>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      {hidden > 0 ? (
        <p className="text-center text-sm text-muted">
          These are the most popular options.{" "}
          <button
            type="button"
            className="font-medium text-accent underline underline-offset-2"
            onClick={() => setShowAll(true)}
          >
            Show {hidden} more
          </button>
        </p>
      ) : null}
    </div>
  );
}
