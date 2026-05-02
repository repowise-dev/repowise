"use client";

import { useEffect, useState } from "react";

/**
 * Debounce a rapidly-changing value. Presentational hook — safe to use
 * inside `@repowise-dev/ui` shells (no data fetching, no framework
 * coupling).
 */
export function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
