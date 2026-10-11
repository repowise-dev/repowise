"use client";

import { useEffect } from "react";

/** Esc drills out one level. Bound globally; bail if focus is in an input. */
export function useC4Keyboard(onEscape: () => void): void {
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || (t && t.isContentEditable)) return;
      onEscape();
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onEscape]);
}
