/**
 * One host read keyed by a string, the way the web page keys its SWR reads:
 * a new key loads again, the last data stays on screen while it does (so
 * paging and filtering do not flash the skeleton) unless `keepPrevious` is
 * off (a drawer must not show the last record under a new one), and `reload`
 * reads the same key again after a write.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface HostData<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

export function useHostData<T>(
  load: () => Promise<T>,
  key: string,
  keepPrevious = true,
): HostData<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  // The loader closes over the render that made it; the key says when to run it.
  const loadRef = useRef(load);
  loadRef.current = load;

  const lastKey = useRef(key);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    if (!keepPrevious && lastKey.current !== key) setData(undefined);
    lastKey.current = key;
    loadRef.current().then(
      (next) => {
        if (cancelled) return;
        setData(next);
        setError(null);
        setLoading(false);
      },
      (err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [key, nonce, keepPrevious]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}
