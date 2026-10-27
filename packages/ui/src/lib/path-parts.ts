/**
 * Pure derivations behind `<FilePath>`: where a repository path splits into
 * directory and file name, and how its monorepo prefix folds for display.
 * Display only: callers always keep the original path for links, keys and
 * the accessible name.
 */

export interface PathParts {
  /** Directory without its trailing slash; empty for a top-level file. */
  dir: string;
  name: string;
}

export function splitPath(path: string): PathParts {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? { dir: "", name: path } : { dir: path.slice(0, cut), name: path.slice(cut + 1) };
}

/** Top-level directories whose next segment names a workspace package. */
const PACKAGE_ROOTS = new Set(["packages", "apps", "libs", "services", "crates"]);

export interface FoldedPath {
  /** The workspace package (`server` for `packages/server/...`), or null when nothing folded. */
  pkg: string | null;
  /** What remains to print after the package chip. */
  rest: string;
}

/** `api-client` and `api_client` name the same package. */
function samePackage(segment: string, pkg: string): boolean {
  const norm = (s: string) => s.toLowerCase().replace(/[-_]/g, "");
  return norm(segment) === norm(pkg);
}

/**
 * `packages/server/src/repowise/server/mcp/tool.py` reads as package `server`
 * plus `mcp/tool.py`: the root, `src/`, and a namespace that repeats the
 * package name (`repowise/server/` or `server/`) say nothing the chip does
 * not. The file name always survives.
 */
export function foldPackagePrefix(path: string): FoldedPath {
  const segments = path.split("/");
  const pkg = segments[1];
  if (segments.length < 3 || !PACKAGE_ROOTS.has(segments[0] ?? "") || !pkg) {
    return { pkg: null, rest: path };
  }
  const inPackage = segments.slice(2);
  const rest = inPackage.length > 1 && inPackage[0] === "src" ? inPackage.slice(1) : inPackage;
  return { pkg, rest: dropRepeatedNamespace(rest, pkg).join("/") };
}

/** `server/x` or `repowise/server/x` under package `server` is just `x`; the name always stays. */
function dropRepeatedNamespace(rest: string[], pkg: string): string[] {
  if (rest.length > 1 && samePackage(rest[0] ?? "", pkg)) return rest.slice(1);
  if (rest.length > 2 && samePackage(rest[1] ?? "", pkg)) return rest.slice(2);
  return rest;
}

/**
 * A directory split for a middle ellipsis: the head clips, the last `keep`
 * segments stay, since the nearest folder is the one a reader recognises.
 */
export function splitDirTail(dir: string, keep = 1): { head: string; tail: string } {
  if (!dir) return { head: "", tail: "" };
  const segments = dir.split("/");
  if (segments.length <= keep) return { head: "", tail: dir };
  return {
    head: segments.slice(0, -keep).join("/"),
    tail: segments.slice(-keep).join("/"),
  };
}

/** `path:line` (or `path:12-40`) as tooltips and assistive tech read it. */
export function pathWithLine(path: string, line?: number | string | null): string {
  return line == null || line === "" ? path : `${path}:${line}`;
}
