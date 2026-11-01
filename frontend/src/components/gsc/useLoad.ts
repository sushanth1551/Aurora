import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../../services/api";

export interface Loaded<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Loads data when `key` changes. A 401 is reported through `onUnauthorized` instead
 * of being shown; other failures stay visible as `error`. Stale responses are ignored.
 */
export function useLoad<T>(
  loader: () => Promise<T>,
  key: unknown,
  onUnauthorized: () => void,
  enabled = true,
): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const latest = useRef(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const unauthorizedRef = useRef(onUnauthorized);
  unauthorizedRef.current = onUnauthorized; // inline callbacks must not retrigger the effect

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    const id = ++latest.current;
    setLoading(true);
    setError(null);
    loaderRef
      .current()
      .then((value) => {
        if (id === latest.current) setData(value);
      })
      .catch((failure: unknown) => {
        if (id !== latest.current) return;
        if (failure instanceof ApiError && failure.status === 401) {
          unauthorizedRef.current();
          return;
        }
        setError(failure instanceof Error ? failure.message : "The request failed.");
      })
      .finally(() => {
        if (id === latest.current) setLoading(false);
      });
    // `key` and `tick` are the intentional triggers; the loader is read through a ref.
  }, [key, tick, enabled]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, loading, error, reload };
}
