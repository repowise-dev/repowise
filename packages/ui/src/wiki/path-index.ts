/**
 * Resolving a code path mentioned in prose to the wiki page that documents it.
 *
 * The backend already resolves *some* inline refs during interlinking and
 * persists them as ``metadata.wiki_links``. That pass only fires for anchors it
 * recognised at generation time, so on a real page most path mentions come back
 * unresolved: the repo overview for this codebase carries 57 distinct inline
 * code spans and 34 wiki_links, of which a handful actually match. Everything
 * else renders as dead text even though a ``file_page`` for that exact path is
 * sitting in the same loaded page list.
 *
 * So resolve the rest here, on the client, from the pages the reader already
 * has. No backend change, no regeneration, and it stays correct as the index
 * grows because it is derived from the live list rather than baked into prose.
 *
 * Two rules keep it honest:
 *
 * - An exact ``target_path`` always wins over a suffix match.
 * - An ambiguous suffix resolves to nothing. Prose says ``models.py`` and this
 *   monorepo has eleven of them; linking to whichever one happened to be
 *   indexed first is worse than not linking at all, because a wrong link is
 *   indistinguishable from a right one until you follow it.
 */

/** The subset of a page this module needs. Structural typing keeps it usable
 *  from both the web app's `PageResponse` and the shared `DocPage`. */
export interface PathIndexablePage {
  id: string;
  target_path?: string | null;
}

export interface PathTarget {
  pageId: string;
  /** The full indexed path, for the link's tooltip. */
  path: string;
}

export interface PathIndex {
  /** Full ``target_path`` → page. */
  exact: Map<string, PathTarget>;
  /** Trailing path fragment → page, or null where the fragment is ambiguous. */
  suffix: Map<string, PathTarget | null>;
}

/**
 * How many trailing segments of a path get their own suffix entry.
 *
 * Four covers what prose actually writes (`spec.py`, `languages/spec.py`,
 * `ingestion/languages/spec.py`) without indexing every prefix of every path
 * in a 5,000-page repo.
 */
const MAX_SUFFIX_SEGMENTS = 4;

/** A symbol page's target is ``path::Name``; those are the interlinker's job. */
function isPathLike(targetPath: string): boolean {
  return targetPath.length > 0 && !targetPath.includes("::");
}

/**
 * Normalise a path for lookup and indexing: no leading ``./``, no repeated or
 * trailing slashes, no trailing glob. Prose writes `packages/ui/src/*` and
 * `ingestion/resolvers/` for the same thing a target_path spells without either.
 */
export function normalizePath(raw: string): string {
  return raw
    .trim()
    .replace(/^\.\//, "")
    .replace(/\/+/g, "/")
    .replace(/\/\*+$/, "")
    .replace(/\/+$/, "");
}

/**
 * True when an inline code span is plausibly a path at all.
 *
 * Inline code is also used for identifiers, flags and prose shorthand, and a
 * bare word must never match a page. Requires either a slash with something
 * after it, or a single filename carrying an extension — which is why `.NET`
 * (no basename before the dot) and `chat` are rejected while `spec.py` is not.
 */
export function looksLikePath(anchor: string): boolean {
  if (!anchor || anchor.length > 200) return false;
  if (/\s/.test(anchor)) return false;
  if (/\/[^/]/.test(anchor)) return true;
  return /^[\w.-]*[\w-]\.[a-zA-Z0-9]{1,6}$/.test(anchor);
}

/** Register a suffix, tombstoning it as ambiguous if a different page claims
 *  it. Once ambiguous it stays ambiguous — a third claimant changes nothing. */
function addSuffix(
  suffix: Map<string, PathTarget | null>,
  key: string,
  target: PathTarget,
): void {
  if (!suffix.has(key)) {
    suffix.set(key, target);
    return;
  }
  const existing = suffix.get(key);
  if (existing && existing.pageId !== target.pageId) suffix.set(key, null);
}

/**
 * Build the lookup from the loaded page list.
 *
 * Linear in total path segments and memoized by the caller on the page list,
 * so it runs once per repo rather than once per render.
 */
export function buildPathIndex(pages: readonly PathIndexablePage[]): PathIndex {
  const exact = new Map<string, PathTarget>();
  const suffix = new Map<string, PathTarget | null>();

  for (const page of pages) {
    const raw = page.target_path;
    if (typeof raw !== "string" || !isPathLike(raw)) continue;
    const path = normalizePath(raw);
    if (!path) continue;

    const target: PathTarget = { pageId: page.id, path };
    // First page to claim a path keeps it. Duplicates are a generator bug,
    // not something to surface in the reader.
    if (!exact.has(path)) exact.set(path, target);

    const segments = path.split("/");
    const start = Math.max(1, segments.length - MAX_SUFFIX_SEGMENTS);
    for (let i = start; i < segments.length; i++) {
      addSuffix(suffix, segments.slice(i).join("/"), target);
    }
  }

  return { exact, suffix };
}

/**
 * Resolve one inline ref. Returns null for anything that is not a path, is
 * unknown, or is ambiguous.
 */
export function resolvePath(
  index: PathIndex,
  anchor: string,
): PathTarget | null {
  if (!looksLikePath(anchor)) return null;
  const path = normalizePath(anchor);
  if (!path) return null;
  const hit = index.exact.get(path);
  if (hit) return hit;
  return index.suffix.get(path) ?? null;
}

/**
 * Shorten a long path for display, keeping the end.
 *
 * The informative part of `packages/core/src/repowise/core/ingestion/models.py`
 * is the tail; the head is the same on every path in the repo and, rendered at
 * full length inline, it breaks the line rhythm of the paragraph around it.
 * The full path stays available as the link's title.
 *
 * Returns the head to be dimmed and the tail to be shown at full strength, so
 * the caller can style them differently without re-parsing.
 */
export function elidePath(
  path: string,
  maxLength = 34,
  keepSegments = 2,
): { head: string; tail: string } {
  if (path.length <= maxLength) return { head: "", tail: path };
  const segments = path.split("/");
  if (segments.length <= keepSegments) return { head: "", tail: path };

  const tail = segments.slice(-keepSegments).join("/");
  // A tail that is already over budget means the basename itself is long;
  // eliding further would cost the only part worth reading.
  return { head: `${segments[0]}/…/`, tail };
}
