/**
 * The current time, refreshed every 30 seconds.
 *
 * "5 minutes ago" needs a clock, and reading `Date.now()` during render makes a
 * component impure. This is an external store instead: one shared interval,
 * started by the first subscriber and stopped by the last.
 */

import { useSyncExternalStore } from "react";

const REFRESH_MS = 30_000;

let current = Date.now();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function tick() {
  current = Date.now();
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    tick();
    timer = setInterval(tick, REFRESH_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function snapshot(): number {
  return current;
}

export function useNow(): number {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
