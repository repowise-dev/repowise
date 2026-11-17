/**
 * The map's keyboard shortcuts as data: which key does what, and the list the
 * help overlay prints. Modelled on the Architecture graph's
 * `useGraphKeyboardShortcuts`: `/` and Ctrl/Cmd+K search, Escape backs out,
 * `?` toggles help; the map adds `f` focus, `b` between and `l` list. Keys
 * typed into a field (Ctrl/Cmd+K aside), pressed inside a dialog, or with Alt
 * or a modifier other than Ctrl/Cmd+K, are not shortcuts. Pure, no React.
 */

import type { SystemMapNavCopy } from "./nav-copy";

export type MapShortcut = "search" | "back" | "focus" | "between" | "list" | "help";

/** The key event fields the map reads; a DOM `KeyboardEvent` fits. */
export interface ShortcutKey {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  /** The element the key was pressed in. */
  target?: EventTarget | null;
  defaultPrevented?: boolean;
}

const PLAIN: Readonly<Record<string, MapShortcut>> = {
  "/": "search",
  Escape: "back",
  f: "focus",
  F: "focus",
  b: "between",
  B: "between",
  l: "list",
  L: "list",
  "?": "help",
};

function typing(target: EventTarget | null | undefined): boolean {
  const el = target as HTMLElement | null | undefined;
  if (!el || typeof el.tagName !== "string") return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable === true;
}

/** A key pressed inside a dialog (a sheet, a menu) belongs to that dialog, even when it does not mark it handled. */
function inDialog(target: EventTarget | null | undefined): boolean {
  const el = target as HTMLElement | null | undefined;
  return typeof el?.closest === "function" && el.closest('[role="dialog"],[role="alertdialog"]') !== null;
}

/**
 * The shortcut a key press asks for, or null. A press something else already
 * handled (the canvas's own Escape, a dialog) is left alone.
 */
export function shortcutFor(e: ShortcutKey): MapShortcut | null {
  if (e.defaultPrevented || e.altKey || inDialog(e.target)) return null;
  // Ctrl/Cmd+K works from any field: the map holds the shortcut while it is on screen.
  if (e.ctrlKey || e.metaKey) return e.key === "k" || e.key === "K" ? "search" : null;
  return typing(e.target) ? null : (PLAIN[e.key] ?? null);
}

/** The help overlay's rows, in reading order. */
export function shortcutHelpRows(copy: SystemMapNavCopy): { keys: string[]; action: string }[] {
  return [
    { keys: ["/"], action: copy.keySearch },
    { keys: ["Ctrl K"], action: copy.keySearch },
    { keys: ["↑", "↓"], action: copy.keySteps },
    { keys: ["Enter"], action: copy.keyPick },
    { keys: ["f"], action: copy.keyFocus },
    { keys: ["b"], action: copy.keyBetween },
    { keys: ["l"], action: copy.keyList },
    { keys: ["Esc"], action: copy.keyBack },
    { keys: ["?"], action: copy.keyHelp },
  ];
}
