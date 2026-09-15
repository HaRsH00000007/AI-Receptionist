"use client";

/**
 * The hero's product visualization: a call arriving, being answered, and
 * turning into a summary.
 *
 * It renders its final state first — which is what the server sends, what a
 * visitor without JavaScript sees, and where reduced-motion visitors stay —
 * and only then, if motion is welcome, loops through the call from the ring.
 */

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { cx } from "@/components/ui/cx";
import { HeadsetIcon, MailIcon, PhoneIncomingIcon } from "@/components/ui/icons";

const LINES = [
  {
    from: "ai",
    text: "Thanks for calling Northside Dental, this is the virtual receptionist. How can I help?",
  },
  { from: "caller", text: "Hi, I chipped a tooth this morning. Can someone see me soon?" },
  {
    from: "ai",
    text: "I’m sorry to hear that. Can I take your name and the best number to reach you?",
  },
  { from: "caller", text: "It’s Maya Reyes, on 555-0142." },
] as const;

/** How long each phase lasts: ringing, four lines, then the summary. */
const PHASE_MS = [1800, 2400, 2600, 2600, 2400, 6000] as const;
const FINAL = PHASE_MS.length - 1;
const CLOCK = ["0:00", "0:05", "0:13", "0:22", "0:31", "0:48"] as const;
const BARS = [10, 18, 24, 14, 20, 9, 16] as const;

export function HeroCallMockup() {
  const [phase, setPhase] = useState<number>(FINAL);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let current = 0;
    let timer: ReturnType<typeof setTimeout>;
    const step = () => {
      setPhase(current);
      timer = setTimeout(() => {
        current = (current + 1) % PHASE_MS.length;
        step();
      }, PHASE_MS[current] ?? 2000);
    };
    timer = setTimeout(step, 600);
    return () => clearTimeout(timer);
  }, []);

  const ringing = phase === 0;
  const ended = phase === FINAL;
  const visibleLines = ringing ? 0 : Math.min(phase, LINES.length);

  return (
    <div
      role="img"
      aria-label="Illustration: an AI receptionist answering a call for a dental practice and writing a call summary"
      className="relative mx-auto w-full max-w-[30rem]"
    >
      <div className="card overflow-hidden rounded-[20px] shadow-xl">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3.5 sm:px-5">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
              <HeadsetIcon size={17} />
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">Northside Dental</p>
              <p className="text-xs text-muted">AI receptionist</p>
            </div>
          </div>
          {ringing ? (
            <Badge tone="accent" dot pulse>
              Incoming call
            </Badge>
          ) : ended ? (
            <Badge tone="neutral">Call ended</Badge>
          ) : (
            <Badge tone="success" dot pulse>
              Live
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-3 px-4 py-4 sm:px-5">
          <div className="relative flex size-11 shrink-0 items-center justify-center rounded-full bg-surface-2 text-sm font-semibold text-ink-2">
            {ringing && (
              <span
                aria-hidden
                className="absolute inset-0 animate-[ring-pulse_1.6s_ease-out_infinite] rounded-full bg-accent/30"
              />
            )}
            MR
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{ringing ? "+1 (555) 014-2231" : "Maya Reyes"}</p>
            <p className="truncate text-xs text-muted">{ringing ? "Answering…" : "+1 (555) 014-2231"}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2.5">
            <div className={cx("waveform h-6 text-accent", (ringing || ended) && "waveform-paused")}>
              {BARS.map((height, index) => (
                <span key={index} style={{ height }} />
              ))}
            </div>
            <span className="w-9 text-right font-mono text-xs tabular-nums text-muted">{CLOCK[phase]}</span>
          </div>
        </div>

        <div className="min-h-[15rem] space-y-2.5 border-t border-line bg-surface-2/60 px-4 py-4 sm:px-5">
          {ringing ? (
            <div className="flex h-[13rem] flex-col items-center justify-center gap-3 text-center">
              <span className="flex size-12 items-center justify-center rounded-full bg-accent text-accent-fg">
                <PhoneIncomingIcon size={22} />
              </span>
              <p className="text-sm font-medium text-ink-2">Answering the call</p>
            </div>
          ) : (
            LINES.slice(0, visibleLines).map((line, index) => (
              <div
                key={index}
                className={cx("flex animate-fade-up", line.from === "caller" && "justify-end")}
              >
                <p
                  className={cx(
                    "max-w-[86%] rounded-2xl px-3.5 py-2 text-[0.8125rem] leading-snug",
                    line.from === "ai"
                      ? "rounded-tl-md border border-line bg-surface text-ink"
                      : "rounded-tr-md bg-accent text-accent-fg",
                  )}
                >
                  {line.text}
                </p>
              </div>
            ))
          )}
        </div>

        <div
          className={cx(
            "border-t border-line px-4 py-4 transition-opacity duration-500 sm:px-5",
            ended ? "opacity-100" : "opacity-0",
          )}
        >
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs font-semibold tracking-wider text-muted uppercase">Call summary</p>
            <Badge tone="warn">Urgent</Badge>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-ink-2">
            Maya chipped a tooth this morning and would like to be seen as soon as possible. She asked
            for a call back.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted">
            <span>Intent: urgent appointment request</span>
            <span className="inline-flex items-center gap-1 font-medium text-success">
              <MailIcon size={14} />
              Summary emailed
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
