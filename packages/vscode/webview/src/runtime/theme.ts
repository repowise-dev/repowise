/**
 * Follows the editor theme. VS Code stamps the webview body with
 * `vscode-light` / `vscode-dark` / `vscode-high-contrast` /
 * `vscode-high-contrast-light`; the shared UI package derives its entire
 * palette from a `.dark` class on the root element. This module keeps the two
 * in sync and exposes a subscription for code that needs the resolved kind
 * (the next-themes shim, canvas renderers).
 */

export type ThemeKind = "light" | "dark";

const subscribers = new Set<(kind: ThemeKind) => void>();
let current: ThemeKind = "light";
let started = false;

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

/** Idempotent; called by the mount helper before first render. */
export function initTheme(): void {
  if (started) return;
  started = true;
  apply(compute());
  const observer = new MutationObserver(() => apply(compute()));
  observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
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
