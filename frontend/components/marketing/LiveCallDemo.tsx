"use client";

/**
 * An example conversation that plays out, with the details the receptionist
 * captures filling in beside it.
 *
 * The whole conversation is rendered first (for the server, for no-JS and for
 * reduced motion). Playback starts once the demo scrolls into view and is
 * restarted by a scenario tab or Replay; every timer belongs to one playback
 * and is cleared when that playback is replaced or the component unmounts.
 */

import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { cx } from "@/components/ui/cx";
import { PlayIcon } from "@/components/ui/icons";

type Speaker = "caller" | "ai";

interface Scenario {
  id: string;
  label: string;
  business: string;
  time: string;
  caller: string;
  callback: string;
  intent: string;
  urgent: boolean;
  outcome: string;
  summary: string;
  /** How many messages must be visible before each detail is known. */
  reveal: { caller: number; intent: number; callback: number };
  messages: ReadonlyArray<{ from: Speaker; text: string }>;
}

const SCENARIOS = [
  {
    id: "booking",
    label: "Appointment request",
    business: "Harbor Street Salon",
    time: "Tue · 2:14 PM",
    caller: "Jordan Price",
    callback: "+1 (555) 018-7702",
    intent: "Appointment request",
    urgent: false,
    outcome: "Request captured",
    summary:
      "Jordan would like a cut and colour tomorrow, late afternoon, and asked for a call back to confirm a time.",
    reveal: { intent: 1, caller: 5, callback: 5 },
    messages: [
      { from: "caller", text: "Hi, I’d like to know if you have appointments available tomorrow." },
      { from: "ai", text: "Absolutely. What time works best for you, and what would you like booked?" },
      { from: "caller", text: "Late afternoon if possible, for a cut and colour." },
      { from: "ai", text: "Got it. Can I take your name and the best number to reach you?" },
      { from: "caller", text: "It’s Jordan Price, on 555-0187." },
      {
        from: "ai",
        text: "Thanks, Jordan. I’ve noted a cut and colour for tomorrow afternoon, and the team will call you to confirm a time.",
      },
    ],
  },
  {
    id: "after-hours",
    label: "After-hours message",
    business: "Carter & Lane Law",
    time: "Thu · 9:42 PM",
    caller: "Sam Ortiz",
    callback: "+1 (555) 016-4410",
    intent: "New client inquiry",
    urgent: false,
    outcome: "Message taken",
    summary:
      "Sam called after hours about a landlord withholding a security deposit and would like a call back from an attorney.",
    reveal: { intent: 1, caller: 3, callback: 5 },
    messages: [
      { from: "caller", text: "Hi, I know it’s late. I need to talk to someone about a problem with my lease." },
      {
        from: "ai",
        text: "You’ve reached Carter & Lane after hours. I can take a message so an attorney can follow up. May I have your name?",
      },
      { from: "caller", text: "Sam Ortiz." },
      { from: "ai", text: "Thanks, Sam. What’s the best number to reach you, and what is the issue about?" },
      { from: "caller", text: "555-0164. My landlord is keeping my deposit." },
      {
        from: "ai",
        text: "Understood. I’ve taken your message, and the office will get back to you during business hours.",
      },
    ],
  },
  {
    id: "urgent",
    label: "Urgent issue",
    business: "Brightway Plumbing",
    time: "Sat · 7:05 AM",
    caller: "Alex Morgan",
    callback: "+1 (555) 012-9934",
    intent: "Emergency repair",
    urgent: true,
    outcome: "Owner notified",
    summary:
      "Water is coming through Alex’s kitchen ceiling at 14 Elm Court and getting worse. Flagged as urgent; call back right away.",
    reveal: { intent: 1, caller: 5, callback: 5 },
    messages: [
      { from: "caller", text: "There’s water coming through my kitchen ceiling. Can someone come out?" },
      { from: "ai", text: "I’m sorry, that sounds urgent. Is the water still coming in right now?" },
      { from: "caller", text: "Yes, and it’s getting worse." },
      {
        from: "ai",
        text: "I’m flagging this as urgent for the team. What’s your name, address and a number to reach you?",
      },
      { from: "caller", text: "Alex Morgan, 14 Elm Court. 555-0129." },
      { from: "ai", text: "Thank you, Alex. I’ve let the team know it’s urgent, and they’ll call you back." },
    ],
  },
] as const satisfies readonly Scenario[];

const FIRST = SCENARIOS[0];

export function LiveCallDemo() {
  const [index, setIndex] = useState(0);
  const [started, setStarted] = useState(false);
  const [run, setRun] = useState(0);
  const [shown, setShown] = useState<number>(FIRST.messages.length);
  const [typing, setTyping] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const baseId = useId();

  const scenario: Scenario = SCENARIOS[index] ?? FIRST;
  const total = scenario.messages.length;
  const done = shown >= total;

  // Start playing the first time the demo is actually on screen.
  useEffect(() => {
    const node = rootRef.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      const timer = setTimeout(() => setStarted(true), 0);
      return () => clearTimeout(timer);
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setStarted(true);
          observer.disconnect();
        }
      },
      { threshold: 0.3 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!started) return;
    const messages = (SCENARIOS[index] ?? FIRST).messages;
    const timers: Array<ReturnType<typeof setTimeout>> = [];

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      timers.push(
        setTimeout(() => {
          setShown(messages.length);
          setTyping(false);
        }, 0),
      );
    } else {
      timers.push(
        setTimeout(() => {
          setShown(0);
          setTyping(true);
        }, 0),
      );
      let elapsed = 0;
      messages.forEach((message, position) => {
        elapsed += 700 + Math.min(message.text.length * 18, 1500);
        timers.push(
          setTimeout(() => {
            setShown(position + 1);
            setTyping(position + 1 < messages.length);
          }, elapsed),
        );
        elapsed += 450;
      });
    }
    return () => timers.forEach(clearTimeout);
  }, [started, index, run]);

  useEffect(() => {
    const node = transcriptRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [shown, typing]);

  function play(next: number) {
    setIndex(next);
    setShown(0);
    setTyping(true);
    setStarted(true);
    setRun((value) => value + 1);
  }

  function onTabKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const next =
      event.key === "ArrowRight"
        ? (index + 1) % SCENARIOS.length
        : (index - 1 + SCENARIOS.length) % SCENARIOS.length;
    play(next);
    document.getElementById(`${baseId}-tab-${next}`)?.focus();
  }

  const nextSpeaker = scenario.messages[shown]?.from ?? "ai";

  return (
    <div ref={rootRef} className="mt-12 grid gap-5 lg:grid-cols-[1.4fr_1fr]">
      <div className="min-w-0 overflow-hidden rounded-[18px] border border-night-line bg-night-2">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-night-line px-3 py-3 sm:px-4">
          <div
            role="tablist"
            aria-label="Example calls"
            onKeyDown={onTabKey}
            className="no-scrollbar flex min-w-0 gap-1 overflow-x-auto"
          >
            {SCENARIOS.map((item, position) => {
              const selected = position === index;
              return (
                <button
                  key={item.id}
                  id={`${baseId}-tab-${position}`}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-controls={`${baseId}-panel`}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => play(position)}
                  className={cx(
                    "shrink-0 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                    selected ? "bg-night-3 text-night-ink" : "text-night-muted hover:text-night-ink",
                  )}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
          <Button
            variant="inverse-ghost"
            size="sm"
            leadingIcon={<PlayIcon size={13} />}
            onClick={() => play(index)}
          >
            Replay
          </Button>
        </div>

        <div id={`${baseId}-panel`} role="tabpanel" aria-labelledby={`${baseId}-tab-${index}`}>
          <div className="flex items-center justify-between gap-3 px-4 pt-4 text-xs text-night-muted sm:px-5">
            <span className="truncate">{scenario.business} · AI receptionist</span>
            <span className="shrink-0">{scenario.time}</span>
          </div>
          <div
            ref={transcriptRef}
            className="h-[23rem] space-y-3 overflow-y-auto px-4 py-4 sm:h-[25rem] sm:px-5"
          >
            {scenario.messages.slice(0, shown).map((message, position) => (
              <Bubble key={`${scenario.id}-${position}`} from={message.from}>
                {message.text}
              </Bubble>
            ))}
            {typing && !done && (
              <Bubble from={nextSpeaker}>
                <span className="typing-dots py-1.5" aria-label="Typing">
                  <span />
                  <span />
                  <span />
                </span>
              </Bubble>
            )}
          </div>
        </div>
      </div>

      <aside
        aria-label="What the receptionist captured"
        className="min-w-0 rounded-[18px] border border-night-line bg-night-2 p-5 sm:p-6"
      >
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-semibold text-night-ink">Captured automatically</p>
          {done ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-400/10 px-2.5 py-1 text-xs font-semibold text-emerald-300">
              Summary emailed
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-[#3651f0]/20 px-2.5 py-1 text-xs font-semibold text-[#b9c3ff]">
              <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden />
              Call in progress
            </span>
          )}
        </div>

        <dl className="mt-5">
          <Detail label="Caller" known={shown >= scenario.reveal.caller}>
            {scenario.caller}
          </Detail>
          <Detail label="Callback number (as stated by caller)" known={shown >= scenario.reveal.callback}>
            <span className="font-mono text-[0.8125rem]">{scenario.callback}</span>
          </Detail>
          <Detail label="Intent" known={shown >= scenario.reveal.intent}>
            {scenario.intent}
          </Detail>
          <Detail label="Urgency" known={shown >= scenario.reveal.intent + 1}>
            {scenario.urgent ? (
              <span className="inline-flex rounded-full bg-amber-400/15 px-2 py-0.5 text-xs font-semibold text-amber-300">
                Urgent
              </span>
            ) : (
              <span className="inline-flex rounded-full bg-night-3 px-2 py-0.5 text-xs font-semibold text-night-ink">
                Routine
              </span>
            )}
          </Detail>
          <Detail label="Outcome" known={done}>
            {scenario.outcome}
          </Detail>
          <Detail label="Summary" known={done}>
            <span className="leading-relaxed">{scenario.summary}</span>
          </Detail>
        </dl>
      </aside>
    </div>
  );
}

function Bubble({ from, children }: { from: Speaker; children: ReactNode }) {
  const caller = from === "caller";
  return (
    <div className={cx("flex animate-fade-up flex-col", caller ? "items-end" : "items-start")}>
      <span className="mb-1 px-1 text-[0.6875rem] font-medium text-night-muted">
        {caller ? "Caller" : "AI receptionist"}
      </span>
      <div
        className={cx(
          "max-w-[88%] rounded-2xl px-3.5 py-2.5 text-sm leading-snug",
          caller ? "rounded-tr-md bg-[#3651f0] text-white" : "rounded-tl-md bg-night-3 text-night-ink",
        )}
      >
        {children}
      </div>
    </div>
  );
}

function Detail({ label, known, children }: { label: string; known: boolean; children: ReactNode }) {
  return (
    <div className="border-t border-night-line py-3.5 first:border-t-0 first:pt-0">
      <dt className="text-xs font-medium text-night-muted">{label}</dt>
      <dd className="mt-1.5 text-sm text-night-ink">
        {known ? (
          <span className="block animate-fade-in">{children}</span>
        ) : (
          <>
            <span className="sr-only">Listening</span>
            <span aria-hidden className="block h-4 w-32 max-w-full animate-pulse rounded bg-night-3" />
          </>
        )}
      </dd>
    </div>
  );
}
