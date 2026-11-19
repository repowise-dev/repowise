/**
 * The "just upgraded" moment, decided once per version in the browser.
 *
 * The last server version this browser saw lives in localStorage. When it
 * changes, a minor release (x.y.0, or any release with highlights) opens the
 * What's new dialog once, and a patch shows a quiet "Updated to vX" note. A
 * fresh browser stores the version and shows nothing; a browser with earlier
 * repowise state but no stored version counts as an upgrade. Highlighted pages carry a
 * "New" mark in the nav until visited, or for 14 days. The welcome
 * (`welcome-moment.ts`) shares a session guard with it: one interruption per
 * session.
 *
 * Every storage call is best-effort: blocked site data means no moment, never
 * an error.
 */

import type { ChangelogHighlight } from "@/lib/api/meta";

const LAST_SEEN_KEY = "repowise:last-seen-server-version";
const NEW_SURFACES_KEY = "repowise:new-surfaces";
const NEW_SURFACES_EVENT = "repowise:new-surfaces";
const LAST_REPO_KEY = "repowise:last-repo";
/** Set once, on the first visit of a browser with no earlier repowise use. */
const NEW_BROWSER_KEY = "repowise:new-browser";
/** One interruption per session: What's new, a patch note or the welcome. */
const SESSION_MOMENT_KEY = "repowise:session-moment";
/** Keys this module writes itself, which say nothing about earlier use. */
const OWN_KEYS = new Set([LAST_SEEN_KEY, NEW_SURFACES_KEY, LAST_REPO_KEY, NEW_BROWSER_KEY]);
export const NEW_MARK_DAYS = 14;

export type UpgradeMoment = "none" | "dialog" | "notice";

/** The numeric parts of a plain release version, or null for anything else
 *  (a dev or rc build is not compared as a release). */
function releaseParts(version: string): number[] | null {
  return /^\d+(\.\d+)*$/.test(version) ? version.split(".").map(Number) : null;
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
  return isMinorRelease(current, hasHighlights) ? "dialog" : "notice";
}

/** x.y.0, or any release that names highlights. */
function isMinorRelease(version: string, hasHighlights: boolean): boolean {
  return hasHighlights || (releaseParts(version)?.[2] ?? 0) === 0;
}

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    // Blocked site data (privacy mode, storage disabled): read as unset.
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

/** Whether this browser used repowise before it stored a version: any of the
 *  app's own keys (banner dismissals, chat and docs state). Browsers that
 *  predate the last-seen key would otherwise count as first visits and never
 *  see the release they upgraded to. */
export function hasPriorUse(): boolean {
  try {
    const store = window.localStorage;
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i);
      if (key && /^repowise[:_]/.test(key) && !OWN_KEYS.has(key)) {
        return true;
      }
    }
  } catch {
    /* blocked site data reads as a fresh browser */
  }
  return false;
}

/** Reads the stored version, stores `current`, and returns the moment due. */
export function takeUpgradeMoment(current: string, hasHighlights: boolean): UpgradeMoment {
  // "0" stands for "some earlier version": prior use with nothing stored.
  const stored = read(LAST_SEEN_KEY) ?? (hasPriorUse() ? "0" : null);
  const moment = upgradeMoment(stored, current, hasHighlights);
  markVersionSeen(current);
  return moment;
}

/** Stores `version` as seen, so the release moment stays quiet for it. */
export function markVersionSeen(version: string): void {
  // Whichever moment stores the first version records a new browser.
  if (isNewBrowser()) write(NEW_BROWSER_KEY, "1");
  write(LAST_SEEN_KEY, version);
}

/** Whether this browser started out with no repowise use: a first visit now,
 *  or one recorded by an earlier visit. A browser that predates this mark has
 *  a stored version or other repowise state, so an upgrading user never
 *  counts as new. */
export function isNewBrowser(): boolean {
  if (read(NEW_BROWSER_KEY) === "1") return true;
  return read(LAST_SEEN_KEY) === null && !hasPriorUse();
}

// --- One moment per session ---------------------------------------------------

/** Fallback for blocked session storage: the guard then lasts for this page load. */
let sessionMomentFallback = false;

/** Whether What's new, a patch note or the welcome already showed this session. */
export function sessionMomentTaken(): boolean {
  try {
    return window.sessionStorage.getItem(SESSION_MOMENT_KEY) === "1";
  } catch {
    return sessionMomentFallback;
  }
}

/** Records that this session had its one interruption. */
export function takeSessionMoment(): void {
  try {
    window.sessionStorage.setItem(SESSION_MOMENT_KEY, "1");
  } catch {
    sessionMomentFallback = true;
  }
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
    // A hand-edited or truncated value: no marks, and the next upgrade rewrites it.
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

/** Remembers the repository the reader last opened, so a highlight opened
 *  from outside a repository (the dashboard, /workspace) still has one. The
 *  first listed repository stands in until one is opened. */
export function rememberRepo(pathname: string | null, firstRepoId: string | null): void {
  const id = repoIdFromPath(pathname);
  if (id) write(LAST_REPO_KEY, id);
  else if (firstRepoId && !read(LAST_REPO_KEY)) write(LAST_REPO_KEY, firstRepoId);
}

/** The repository a repo-scoped highlight should open: the current page's,
 *  else the last one opened. */
export function highlightRepoId(pathname: string | null): string | null {
  return repoIdFromPath(pathname) ?? read(LAST_REPO_KEY);
}

/** `?whats-new=preview` replays the minor-release moment, `?whats-new=patch`
 *  the patch note, without touching what this browser has seen. */
const PREVIEWS: Record<string, UpgradeMoment> = { preview: "dialog", patch: "notice" };

export function previewMoment(search: string): UpgradeMoment | null {
  return PREVIEWS[new URLSearchParams(search).get("whats-new") ?? ""] ?? null;
}

/** The moment due on load: a `?whats-new=` replay, or the real upgrade check,
 *  which also starts the "New" marks for that release. When the welcome
 *  already showed this session, the upgrade waits for the next one. */
export function decideMoment(
  version: string,
  highlights: ChangelogHighlight[],
  search: string,
): { moment: UpgradeMoment; preview: boolean } {
  const replay = previewMoment(search);
  if (replay) return { moment: replay, preview: true };
  // `?tour=1` opens the welcome on this load: one dialog at a time, and the
  // upgrade (left unstored) waits for the next session.
  if (sessionMomentTaken() || new URLSearchParams(search).get("tour") === "1") {
    return { moment: "none", preview: false };
  }
  const moment = takeUpgradeMoment(version, highlights.length > 0);
  if (moment !== "none") {
    startNewSurfaces(version, highlights);
    takeSessionMoment();
  }
  return { moment, preview: false };
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
