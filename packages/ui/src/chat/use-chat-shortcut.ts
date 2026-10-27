"use client";

import { useEffect, useRef } from "react";

/** The one place the shortcut is named, so the empty state and the handler
 *  cannot drift apart. */
export const CHAT_SHORTCUT_KEY = "?";
export const CHAT_SHORTCUT_HINT = "Press ? from any page to open chat";

/** `closest`, not a tag check: a caret inside a nested span of a rich-text
 *  editor is still typing, and jsdom never sets `isContentEditable`. The ARIA
 *  roles are here because a combobox or textbox widget can accept typing
 *  without being any of the native elements. */
const TYPING_SELECTOR = [
  "input",
  "textarea",
  "select",
  '[contenteditable=""]',
  '[contenteditable="true"]',
  '[role="textbox"]',
  '[role="searchbox"]',
  '[role="combobox"]',
].join(", ");

function isTypingTarget(event: KeyboardEvent): boolean {
  // `composedPath` first: `target` is retargeted to the host for anything
  // inside a shadow root, which would hide a real input behind it.
  const origin = event.composedPath?.()[0] ?? event.target;
  return origin instanceof Element && origin.closest(TYPING_SELECTOR) !== null;
}

/**
 * Registers `Shift+/` once for the whole repository shell.
 *
 * Deliberately unmodified: a bare `?` is free everywhere in the product, and
 * anything with a platform modifier belongs to the browser or the editor.
 */
export function useChatShortcut(onTrigger: () => void, enabled = true): void {
  // Held in a ref so an unmemoized caller does not re-register the global
  // listener on every render.
  const latest = useRef(onTrigger);
  latest.current = onTrigger;

  useEffect(() => {
    if (!enabled) return undefined;
    const handle = (event: KeyboardEvent) => {
      if (event.key !== CHAT_SHORTCUT_KEY) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.defaultPrevented) return;
      if (isTypingTarget(event)) return;
      event.preventDefault();
      latest.current();
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [enabled]);
}
