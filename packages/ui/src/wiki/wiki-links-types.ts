/**
 * Typed accessors for page.metadata.wiki_links and page.metadata.backlinks.
 *
 * Both fields are populated by the backend's interlinking post-processor
 * (`packages/core/.../generation/interlinking.py`). They live inside the
 * generic `metadata: Record<string, unknown>` blob so the API schema
 * doesn't need a breaking change every time the backend learns to
 * resolve a new kind of reference.
 *
 * The accessors below are defensive — they tolerate missing fields,
 * legacy pages without metadata, and unexpected shapes.
 */

export type WikiLinkKind = "file" | "symbol" | "page";

/**
 * Forward wiki-link entry persisted in ``page.metadata.wiki_links``.
 *
 * Note: deliberately named ``WikiLinkRef`` (not ``WikiLink``) to avoid
 * a name collision with the ``WikiLink`` React component re-exported
 * from the same package barrel.
 */
export interface WikiLinkRef {
  anchor: string;
  target_page_id: string;
  kind: WikiLinkKind;
}

export interface Backlink {
  source_page_id: string;
  source_title: string;
  source_page_type: string;
  anchor: string;
}

/**
 * Graph-derived neighbor persisted in ``page.metadata.related_pages`` by
 * the backend's related-pages post-processor
 * (`packages/core/.../generation/related_pages.py`). Unlike wiki_links,
 * these do not depend on the page prose mentioning the target.
 */
export type RelatedReason =
  | "imports"
  | "imported-by"
  | "co-changes-with"
  | "same-module";

export interface RelatedPageRef {
  target_page_id: string;
  title: string;
  reason: RelatedReason;
  weight: number;
}

function isWikiLinkRef(v: unknown): v is WikiLinkRef {
  if (typeof v !== "object" || v === null) return false;
  const r = v as Record<string, unknown>;
  return (
    typeof r["anchor"] === "string" &&
    typeof r["target_page_id"] === "string" &&
    typeof r["kind"] === "string"
  );
}

function isBacklink(v: unknown): v is Backlink {
  if (typeof v !== "object" || v === null) return false;
  const r = v as Record<string, unknown>;
  return (
    typeof r["source_page_id"] === "string" &&
    typeof r["source_title"] === "string" &&
    typeof r["source_page_type"] === "string"
  );
}

export function getWikiLinks(
  metadata: Record<string, unknown> | undefined | null,
): WikiLinkRef[] {
  if (!metadata) return [];
  const raw = metadata["wiki_links"];
  if (!Array.isArray(raw)) return [];
  return raw.filter(isWikiLinkRef);
}

const RELATED_REASONS = new Set([
  "imports",
  "imported-by",
  "co-changes-with",
  "same-module",
]);

function isRelatedPageRef(v: unknown): v is RelatedPageRef {
  if (typeof v !== "object" || v === null) return false;
  const r = v as Record<string, unknown>;
  return (
    typeof r["target_page_id"] === "string" &&
    typeof r["title"] === "string" &&
    typeof r["reason"] === "string" &&
    RELATED_REASONS.has(r["reason"])
  );
}

export function getRelatedPages(
  metadata: Record<string, unknown> | undefined | null,
): RelatedPageRef[] {
  if (!metadata) return [];
  const raw = metadata["related_pages"];
  if (!Array.isArray(raw)) return [];
  return raw.filter(isRelatedPageRef);
}

export function getBacklinks(
  metadata: Record<string, unknown> | undefined | null,
): Backlink[] {
  if (!metadata) return [];
  const raw = metadata["backlinks"];
  if (!Array.isArray(raw)) return [];
  return raw.filter(isBacklink);
}

/**
 * Build a lookup: anchor → target_page_id for fast O(1) rewrite inside
 * the MDX preprocessor. Multiple anchors mapping to the same target are
 * fine; conflicting anchors are resolved first-wins.
 */
export function buildAnchorIndex(
  links: WikiLinkRef[],
): Map<string, { pageId: string; kind: WikiLinkKind }> {
  const idx = new Map<string, { pageId: string; kind: WikiLinkKind }>();
  for (const link of links) {
    if (!idx.has(link.anchor)) {
      idx.set(link.anchor, { pageId: link.target_page_id, kind: link.kind });
    }
  }
  return idx;
}
