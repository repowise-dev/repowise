/**
 * The "just upgraded" moment, decided once per version in the browser.
 *
 * The last server version this browser saw lives in localStorage. When it
 * changes, a minor release (x.y.0, or any release with highlights) opens the
 * What's new dialog once, and a patch shows a quiet "Updated to vX" note. A
 * first visit stores the version and shows nothing. Highlighted pages carry a
 * "New" mark in the nav until visited, or for 14 days.
 *
 * Every storage call is best-effort: blocked site data means no moment, never
 * an error.
 */

import type { ChangelogHighlight } from "@/lib/api/meta";

const LAST_SEEN_KEY = "repowise:last-seen-server-version";
const NEW_SURFACES_KEY = "repowise:new-surfaces";
const NEW_SURFACES_EVENT = "repowise:new-surfaces";
export const NEW_MARK_DAYS = 14;

export type UpgradeMoment = "none" | "dialog" | "notice";

/** The numeric parts of a version, or null when it has none. */
function releaseParts(version: string): number[] | null {
  const parts = version.split(".").map((p) => parseInt(p, 10));
  return parts.length && parts.every((n) => Number.isFinite(n)) ? parts : null;
}

function isNewer(next: string, prev: string): boolean {
  const a = releaseParts(next);
  const b = releaseParts(prev);
  if (!a || !b) return false;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0);
    if (d !== 0) return d > 0;
  }
  return false;
}

/** What to show for a move from `stored` to `current`. A first visit
 *  (`stored` null) and a downgrade show nothing. */
export function upgradeMoment(
  stored: string | null,
  current: string,
  hasHighlights: boolean,
): UpgradeMoment {
  if (!stored || stored === current || !isNewer(current, stored)) return "none";
  const parts = releaseParts(current);
  const minor = hasHighlights || (parts !== null && (parts[2] ?? 0) === 0);
  return minor ? "dialog" : "notice";
}

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* blocked site data: the moment simply does not persist */
  }
}

/** Reads the stored version, stores `current`, and returns the moment due. */
export function takeUpgradeMoment(current: string, hasHighlights: boolean): UpgradeMoment {
  const moment = upgradeMoment(read(LAST_SEEN_KEY), current, hasHighlights);
  write(LAST_SEEN_KEY, current);
  return moment;
}

// --- "New" marks ------------------------------------------------------------

export interface NewSurfaces {
  version: string;
  /** When the upgrade was first seen, ms since epoch. */
  since: number;
  /** Highlight routes, with `:id` left in for repo-scoped pages. */
  routes: string[];
  visited: string[];
}

export function readNewSurfaces(): NewSurfaces | null {
  const raw = read(NEW_SURFACES_KEY);
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as NewSurfaces;
    return Array.isArray(v.routes) && Array.isArray(v.visited) ? v : null;
  } catch {
    return null;
  }
}

function saveNewSurfaces(value: NewSurfaces): void {
  write(NEW_SURFACES_KEY, JSON.stringify(value));
  window.dispatchEvent(new Event(NEW_SURFACES_EVENT));
}

export function startNewSurfaces(version: string, highlights: ChangelogHighlight[]): void {
  const routes = highlights.map((h) => h.href).filter((h): h is string => !!h);
  saveNewSurfaces({ version, since: Date.now(), routes, visited: [] });
}

/** `/repos/abc/chat/123` matches `/repos/:id/chat`. */
export function routeMatches(route: string, pathname: string): boolean {
  const want = route.split("/").filter(Boolean);
  const have = pathname.split("/").filter(Boolean);
  if (have.length < want.length) return false;
  return want.every((p, i) => p === ":id" || p === have[i]);
}

/** Routes still marked "New" for `now`. */
export function activeNewRoutes(value: NewSurfaces | null, now: number): string[] {
  if (!value || now - value.since > NEW_MARK_DAYS * 24 * 60 * 60 * 1000) return [];
  return value.routes.filter((r) => !value.visited.includes(r));
}

/** Records a visit to `pathname`, clearing the mark of the route it opens. */
export function visitNewSurface(pathname: string): void {
  const value = readNewSurfaces();
  const hit = activeNewRoutes(value, Date.now()).find((r) => routeMatches(r, pathname));
  if (!value || !hit) return;
  saveNewSurfaces({ ...value, visited: [...value.visited, hit] });
}

export function subscribeNewSurfaces(onChange: () => void): () => void {
  window.addEventListener(NEW_SURFACES_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(NEW_SURFACES_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** A highlight's route for the current repository, or null when it needs one
 *  and there is none. */
export function resolveHighlightHref(href: string | null | undefined, repoId: string | null): string | null {
  if (!href) return null;
  if (!href.includes(":id")) return href;
  return repoId ? href.replace(":id", encodeURIComponent(repoId)) : null;
}

/** The repository the current page belongs to, from `/repos/<id>/...`. */
export function repoIdFromPath(pathname: string | null): string | null {
  const m = pathname?.match(/^\/repos\/([^/]+)/);
  return m?.[1] ? decodeURIComponent(m[1]) : null;
}

/** `?whats-new=preview` replays the minor-release moment, `?whats-new=patch`
 *  the patch note, without touching what this browser has seen. */
export function previewMoment(search: string): UpgradeMoment | null {
  const v = new URLSearchParams(search).get("whats-new");
  return v === "preview" ? "dialog" : v === "patch" ? "notice" : null;
}

/** `#123` in changelog prose links to its pull request, outside code spans. */
export function linkPullRequests(markdown: string): string {
  return markdown
    .split(/(`[^`]*`)/)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(
            /(^|[^\w[/&])#(\d+)\b/g,
            (_m, pre: string, n: string) =>
              `${pre}[#${n}](https://github.com/repowise-dev/repowise/pull/${n})`,
          ),
    )
    .join("");
}
