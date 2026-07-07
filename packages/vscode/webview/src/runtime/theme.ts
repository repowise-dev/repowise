/**
 * Follows the editor theme. VS Code stamps the webview body with
 * `vscode-light` / `vscode-dark` / `vscode-high-contrast` /
 * `vscode-high-contrast-light`; the shared UI package derives its entire
 * palette from a `.dark` class on the root element. This module keeps the two
 * in sync and exposes a subscription for code that needs the resolved kind
 * (the next-themes shim, canvas renderers).
 */

import type { ThemePreference } from "../../../src/shared/webviewMessages";

export type ThemeKind = "light" | "dark";

const subscribers = new Set<(kind: ThemeKind) => void>();
let current: ThemeKind = "light";
let started = false;
/** Pinned scheme from the Home switcher; "auto" follows the editor. */
let preference: ThemePreference = "auto";

function compute(): ThemeKind {
  const cls = document.body.classList;
  // High-contrast without the -light suffix is the dark HC theme.
  if (cls.contains("vscode-dark")) return "dark";
  if (cls.contains("vscode-high-contrast") && !cls.contains("vscode-high-contrast-light")) {
    return "dark";
  }
  return "light";
}

function apply(kind: ThemeKind): void {
  document.documentElement.classList.toggle("dark", kind === "dark");
  if (kind === current) return;
  current = kind;
  for (const cb of subscribers) cb(kind);
}

/** The effective kind under the current preference. */
function resolve(): ThemeKind {
  return preference === "auto" ? compute() : preference;
}

/** Idempotent; called by the mount helper before first render. */
export function initTheme(): void {
  if (started) return;
  started = true;
  apply(resolve());
  const observer = new MutationObserver(() => apply(resolve()));
  observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
}

/**
 * Applies the persisted preference pushed by the host (init and theme-changed
 * messages). A pinned value wins over the editor theme until set back to auto.
 */
export function setThemePreference(pref: ThemePreference): void {
  preference = pref;
  apply(resolve());
}

export function getThemePreference(): ThemePreference {
  return preference;
}

export function getThemeKind(): ThemeKind {
  return current;
}

/**
 * User-initiated override from an in-view toggle (e.g. the graph's light/dark
 * switch). Applies immediately; the next editor theme change wins again via
 * the body-class observer.
 */
export function setThemeOverride(kind: ThemeKind): void {
  apply(kind);
}

export function subscribeTheme(cb: (kind: ThemeKind) => void): () => void {
  subscribers.add(cb);
  return () => subscribers.delete(cb);
}
