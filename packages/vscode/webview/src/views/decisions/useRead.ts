/**
 * One host read keyed like an SWR call, for a view whose writes must move
 * every figure on screen together.
 *
 * Ceiling: no shared cache and no dedupe across components. The board's reads
 * are few and each key has one reader, so a key plus an `epoch` the view bumps
 * after a write is all the revalidation it needs. Upgrade path is a small
 * keyed store if two components ever need the same read.
 */

import { useCallback, useEffect, useRef, useState } from "react";

interface ReadState<T> {
  key: string | null;
  data?: T;
  error?: unknown;
  loading: boolean;
}

export interface Read<T> {
  data: T | undefined;
  error: unknown;
  /** True only while the first answer for this key is in flight. */
  isLoading: boolean;
  reload: () => void;
}

/**
 * `key` null skips the read. A new key drops the old key's data, so a lane
 * switch never shows the previous lane's rows; a new `epoch` refetches the
 * same key and keeps its rows on screen until the fresh ones land.
 */
export function useRead<T>(key: string | null, load: () => Promise<T>, epoch: string | number): Read<T> {
  const [state, setState] = useState<ReadState<T>>({ key, loading: key !== null });
  const loadRef = useRef(load);
  loadRef.current = load;
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (key === null) {
      setState({ key, loading: false });
      return;
    }
    let cancelled = false;
    setState((prev) => (prev.key === key ? { ...prev, loading: true } : { key, loading: true }));
    loadRef.current().then(
      (data) => {
        if (!cancelled) setState({ key, data, loading: false });
      },
      (error: unknown) => {
        if (cancelled) return;
        setState((prev) => ({
          key,
          ...(prev.key === key && prev.data !== undefined ? { data: prev.data } : {}),
          error,
          loading: false,
        }));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [key, epoch, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const current: ReadState<T> = state.key === key ? state : { key, loading: key !== null };
  return {
    data: current.data,
    error: current.error,
    isLoading: current.loading && current.data === undefined,
    reload,
  };
}
