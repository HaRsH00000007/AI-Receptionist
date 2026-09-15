"use client";

/**
 * Transient confirmations ("Copied", "Retry queued").
 *
 * The region is `aria-live="polite"` rather than `role="status"`, so a toast is
 * announced without adding a second status landmark to pages that already
 * have one. Anything the user must act on belongs in an `Alert`, not here.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { cx } from "./cx";
import { CheckCircleIcon, InfoIcon, XCircleIcon } from "./icons";

type ToastTone = "success" | "danger" | "info";

interface ToastInput {
  title: string;
  description?: string;
  tone?: ToastTone;
}

interface ToastEntry extends ToastInput {
  id: number;
}

const ToastContext = createContext<(toast: ToastInput) => void>(() => {});

const DISMISS_AFTER_MS = 4_500;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const nextId = useRef(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending) clearTimeout(timer);
    };
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (toast: ToastInput) => {
      const id = nextId.current++;
      setToasts((current) => [...current.slice(-2), { ...toast, id }]);
      const timer = setTimeout(() => {
        timers.current.delete(timer);
        dismiss(id);
      }, DISMISS_AFTER_MS);
      timers.current.add(timer);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-20 z-[80] flex flex-col items-center gap-2 px-4 sm:inset-x-auto sm:right-5 sm:bottom-5 sm:items-end"
      >
        {toasts.map((toast) => {
          const tone = toast.tone ?? "success";
          const Icon = tone === "danger" ? XCircleIcon : tone === "info" ? InfoIcon : CheckCircleIcon;
          return (
            <div
              key={toast.id}
              className="toast pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border border-line bg-surface px-4 py-3 shadow-lg"
            >
              <Icon
                size={18}
                className={cx(
                  "mt-0.5 shrink-0",
                  tone === "danger" ? "text-danger" : tone === "info" ? "text-accent" : "text-success",
                )}
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold">{toast.title}</p>
                {toast.description && <p className="mt-0.5 text-sm text-muted">{toast.description}</p>}
              </div>
              <button
                type="button"
                onClick={() => dismiss(toast.id)}
                className="text-xs font-medium text-muted hover:text-ink"
              >
                Dismiss
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
