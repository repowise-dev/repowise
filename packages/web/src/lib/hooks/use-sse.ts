"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface SSEState<T> {
  data: T | null;
  /** Accumulated default-type frames (`event: message`); the job streams
   * use these for pipeline log lines, interleaved with progress frames. */
  messages: unknown[];
  error: Error | null;
  isConnected: boolean;
  isDone: boolean;
}

// Matches the server's per-job ring buffer so a pathological stream can't
// grow client memory unbounded.
const MAX_MESSAGES = 500;

/**
 * Generic Server-Sent Events hook.
 * Connects to the given URL when enabled=true.
 * Parses events with format: "event: <name>\ndata: <json>\n\n"
 */
export function useSSE<T>(
  url: string | null,
  opts?: { enabled?: boolean; maxRetries?: number },
): SSEState<T> & { close: () => void } {
  const [state, setState] = useState<SSEState<T>>({
    data: null,
    messages: [],
    error: null,
    isConnected: false,
    isDone: false,
  });
  const esRef = useRef<EventSource | null>(null);
  const retriesRef = useRef(0);
  const maxRetries = opts?.maxRetries ?? 3;
  const enabled = opts?.enabled !== false;

  const close = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    setState((s) => ({ ...s, isConnected: false }));
  }, []);

  useEffect(() => {
    if (!url || !enabled) return;

    retriesRef.current = 0;

    function connect() {
      const es = new EventSource(url as string);
      esRef.current = es;

      es.onopen = () => {
        setState((s) => ({ ...s, isConnected: true, error: null }));
        retriesRef.current = 0;
      };

      // `event: message` is the default SSE event type, so it arrives here
      // rather than via addEventListener. These frames are log lines, not
      // progress snapshots; collect them instead of overwriting `data`.
      es.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data) as unknown;
          setState((s) => ({
            ...s,
            messages: [...s.messages, parsed].slice(-MAX_MESSAGES),
          }));
        } catch {
          // non-JSON message, ignore
        }
      };

      // Named event handlers for "progress", "done", "error"
      es.addEventListener("progress", (e) => {
        try {
          setState((s) => ({ ...s, data: JSON.parse((e as MessageEvent).data) as T }));
        } catch { /* ignore */ }
      });

      es.addEventListener("done", (e) => {
        try {
          setState((s) => ({
            ...s,
            data: JSON.parse((e as MessageEvent).data) as T,
            isDone: true,
            isConnected: false,
          }));
        } catch { /* ignore */ }
        es.close();
      });

      es.addEventListener("error", (e) => {
        try {
          const parsed = JSON.parse((e as MessageEvent).data) as { error: string };
          setState((s) => ({
            ...s,
            error: new Error(parsed.error),
            isConnected: false,
            isDone: true,
          }));
        } catch { /* ignore */ }
        es.close();
      });

      es.onerror = () => {
        es.close();
        esRef.current = null;
        setState((s) => ({ ...s, isConnected: false }));
        if (retriesRef.current < maxRetries) {
          retriesRef.current++;
          const delay = Math.pow(2, retriesRef.current) * 1000;
          setTimeout(connect, delay);
        } else {
          setState((s) => ({
            ...s,
            error: new Error("SSE connection failed after max retries"),
          }));
        }
      };
    }

    connect();

    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, [url, enabled, maxRetries]);

  return { ...state, close };
}
