"use client";

import { useEffect, useState } from "react";

/**
 * Tracks `prefers-reduced-motion: reduce`.
 *
 * Only for motion CSS cannot reach — a JS-driven animation, a Lottie that
 * autoplays, a canvas loop. Anything expressible in CSS should use the
 * `motion-safe:` / `motion-reduce:` variants instead, which need no state and
 * no hydration pass.
 *
 * Starts `false` so server and first client render agree; the effect
 * corrects it on mount.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return reduced;
}
