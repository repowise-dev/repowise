/**
 * A page's citations, read off `metadata.sources`.
 *
 * Core writes one row per cited source: `{path, kind}` always, a line span and
 * the symbol it covers when the evidence carried one, and a forge `url` pinned
 * to the commit the page was generated at when the repository has a known
 * remote (`metadata.sources_commit` names that commit). Every field past
 * `path` is optional, so a page written before spans existed reads as it did.
 */

export interface Citation {
  path: string;
  kind: string;
  /** `"12-40"`, `"12"`, or `""` for a whole-file citation. */
  lines: string;
  startLine?: number;
  symbol?: string;
  /** Forge URL at the generation commit, when the repo has a known remote. */
  url?: string;
  /** The wiki page documenting `path`, when there is one. */
  pageId?: string;
}

interface RawSource {
  path?: unknown;
  kind?: unknown;
  start_line?: unknown;
  end_line?: unknown;
  symbol?: unknown;
  url?: unknown;
}

const asInt = (v: unknown): number | undefined =>
  typeof v === "number" && Number.isInteger(v) && v > 0 ? v : undefined;

const asText = (v: unknown): string | undefined =>
  typeof v === "string" && v.length > 0 ? v : undefined;

function lineRange(start: number | undefined, end: number | undefined): string {
  if (start === undefined) return "";
  return end !== undefined && end > start ? `${start}-${end}` : String(start);
}

export function readCitations(
  sources: unknown,
  pageIdForPath: (path: string) => string | undefined,
): Citation[] {
  if (!Array.isArray(sources)) return [];
  const out: Citation[] = [];
  for (const raw of sources as RawSource[]) {
    const path = asText(raw?.path);
    if (!path) continue;
    const start = asInt(raw.start_line);
    const symbol = asText(raw.symbol);
    const url = asText(raw.url);
    const pageId = pageIdForPath(path);
    out.push({
      path,
      kind: asText(raw.kind) ?? "",
      lines: lineRange(start, asInt(raw.end_line)),
      ...(start !== undefined ? { startLine: start } : {}),
      ...(symbol ? { symbol } : {}),
      ...(url ? { url } : {}),
      ...(pageId ? { pageId } : {}),
    });
  }
  return out;
}

/** `path:12-40`, or the bare path for a whole-file citation. */
export const citationLabel = (c: Citation): string => (c.lines ? `${c.path}:${c.lines}` : c.path);

/** Several spans in one file are distinct citations; the key keeps them apart. */
export const citationKey = (c: Citation): string => `${c.path}:${c.lines}:${c.symbol ?? ""}`;
