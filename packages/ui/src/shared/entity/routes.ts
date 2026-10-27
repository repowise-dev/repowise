import type { EntityKind, EntityRef } from "./types";

/** Encode a repo-relative file path for use inside a route, keeping slashes. */
export function encodeFilePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

/** Canonical file entity page path (relative to the repo link prefix). */
export function fileEntityPath(prefix: string, filePath: string): string {
  return `${prefix}/files/${encodeFilePath(filePath)}`;
}

/** Canonical symbol entity page path (relative to the repo link prefix). */
export function symbolEntityPath(prefix: string, symbolId: string): string {
  return `${prefix}/symbols/${encodeURIComponent(symbolId)}`;
}

/** The wiki page id a file's documentation is stored under.
 *
 *  The literal was hardcoded in five places that all had to agree with the
 *  backend's `"{page_type}:{target_path}"` primary key, and one of them
 *  (`pageHref`) had to parse it back off again. */
export function filePageId(filePath: string): string {
  return `file_page:${filePath}`;
}

/** Canonical route to a wiki page inside the docs reading surface.
 *
 *  `/docs?page=` and not `/wiki/<id>`: the docs route keeps the tree and the
 *  rail around the page, and the standalone wiki route redirects into it
 *  anyway. */
export function docsPagePath(prefix: string, pageId: string): string {
  return `${prefix}/docs?page=${encodeURIComponent(pageId)}`;
}

/**
 * Resolve the canonical href for an entity. Centralizing this here keeps every
 * link consistent and makes the route map auditable in one place.
 *
 * `repoId` is optional but required for file/symbol routing. When missing we
 * fall back to a relative anchor so callers can still render the link without
 * a known repo (e.g. inside cross-repo widgets).
 */
export function resolveEntityHref(ref: EntityRef): string {
  const { kind, id, repoId } = ref;
  if (!repoId && (kind === "file" || kind === "symbol" || kind === "decision")) {
    return `#${kind}:${encodeURIComponent(id)}`;
  }

  switch (kind) {
    case "file":
      return fileEntityPath(`/repos/${repoId}`, id);
    case "symbol":
      return symbolEntityPath(`/repos/${repoId}`, id);
    case "decision":
      return `/repos/${repoId}/decisions/${encodeURIComponent(id)}`;
    case "owner":
      return repoId
        ? `/repos/${repoId}/owners/${encodeURIComponent(id)}`
        : `#owner:${encodeURIComponent(id)}`;
    case "commit":
      return repoId
        ? `/repos/${repoId}/commits?commit=${encodeURIComponent(id)}`
        : `#commit:${encodeURIComponent(id)}`;
  }
}

/** Prefer a short, readable label for the entity (used as default link text). */
export function defaultEntityLabel(ref: EntityRef): string {
  const { kind, id } = ref;
  switch (kind) {
    case "file":
      return id.split("/").slice(-1)[0] || id;
    case "symbol": {
      const tail = id.split("::").slice(-1)[0] || id;
      return tail.split(".").slice(-1)[0] || tail;
    }
    case "owner":
      return id.includes("@") ? (id.split("@")[0] ?? id) : id;
    case "commit":
      return id.length > 7 ? id.slice(0, 7) : id;
    default:
      return id;
  }
}

/** Icon hint used by EntityLink and CommandPalette when no children supplied. */
export const ENTITY_KIND_LABEL: Record<EntityKind, string> = {
  file: "File",
  symbol: "Symbol",
  decision: "Decision",
  owner: "Owner",
  commit: "Commit",
};
