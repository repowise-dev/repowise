"use client";

import { useEffect } from "react";

interface PresentKeys {
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  onFirst?: () => void;
  onLast?: () => void;
}

/**
 * Global keyboard navigation for the Present overlay: ←/PageUp = prev,
 * →/PageDown/Space = next, Home/End = first/last, Escape = close. Ignores key
 * events while a text input/textarea is focused so nothing is hijacked.
 */
export function usePresentKeyboard({ onPrev, onNext, onClose, onFirst, onLast }: PresentKeys) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }
      switch (e.key) {
        case "ArrowRight":
        case "PageDown":
        case " ":
          e.preventDefault();
          onNext();
          break;
        case "ArrowLeft":
        case "PageUp":
          e.preventDefault();
          onPrev();
          break;
        case "Home":
          if (onFirst) {
            e.preventDefault();
            onFirst();
          }
          break;
        case "End":
          if (onLast) {
            e.preventDefault();
            onLast();
          }
          break;
        case "Escape":
          e.preventDefault();
          onClose();
          break;
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onPrev, onNext, onClose, onFirst, onLast]);
}
