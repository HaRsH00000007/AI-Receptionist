"use client";

/**
 * Load one thing for a page, with the states a page needs: loading, loaded,
 * failed, and a way to load it again.
 *
 * For data one page needs and the rest of the workspace does not — contacts,
 * integrations, the SMS registration. Shared data lives in `TenantWorkspace`.
 * A response that arrives after the page has moved on (or after a newer
 * request) is dropped rather than rendered over fresher data.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
  /** Replace the data with a fresher copy the page already has (after a save). */
  set: (value: T) => void;
}

export function useLoad<T>(loader: () => Promise<T>, key: string): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  // The latest loader, read inside the effect, so an inline arrow function
  // does not re-run the effect on every render. `key` is what re-runs it.
  const loaderRef = useRef(loader);
  useEffect(() => {
    loaderRef.current = loader;
  });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const value = await loaderRef.current();
        if (cancelled) return;
        setData(value);
        setError(null);
      } catch (caught) {
        if (cancelled) return;
        setError(caught instanceof ApiError ? caught.message : "Something went wrong loading this.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [key, nonce]);

  const reload = useCallback(() => {
    setLoading(true);
    setNonce((value) => value + 1);
  }, []);
  const set = useCallback((value: T) => setData(value), []);

  return { data, error, loading, reload, set };
}
