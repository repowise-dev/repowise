/**
 * What a Repowise reply carried, read from the MCP text Claude received.
 * Pure. Each tool has a small reader for the evidence it returns; an unknown
 * tool, or a shape a reader does not know, falls back to the reply's own
 * top-level lists. Only fields the reply has are read; nothing is inferred.
 */

/** Characters of a reply parsed: larger replies are summarized by size alone. */
export const PARSE_CHARS = 64 * 1024;
/** Characters of a reply kept for display. */
export const EXCERPT_CHARS = 2 * 1024;
/** Paths kept per reply for "led to". */
const MAX_PATHS = 200;
/** How deep the path walk goes into a reply. */
const MAX_DEPTH = 6;

type Rec = Record<string, unknown>;

/**
 * One count the reply carried. `what` is a known evidence kind
 * (views/copy.ts names its unit) or, from the fallback, the reply's own key.
 */
export interface ReplyFact {
  what: string;
  n: number;
  /** The total when the reply said it showed only part. */
  of?: number;
}

/** The reply's own account of where it came from and how complete it is. */
export interface Behind {
  commit: string | null;
  ageDays: number | null;
  /** The local server's word on whether the index is behind the checkout; null when it did not say. */
  indexBehind: boolean | null;
  verified: boolean | null;
  complete: boolean | null;
  confidence: string | null;
  grounding: string | null;
  retrieval: string | null;
  /** Characters served against the response budget. */
  budget: { used: number; limit: number } | null;
  /** Tokens the server left out, restorable by ref. */
  omittedTokens: number | null;
  /** Why the reply is not the full answer (`no-llm-provider`). */
  degraded: string | null;
  semantic: boolean | null;
}

export interface ReplySummary {
  tool: string;
  /** UTF-8 bytes of the reply text; null when the text was not seen. */
  bytes: number | null;
  /** False when the text was not JSON or too large to parse. */
  parsed: boolean;
  inside: ReplyFact[];
  behind: Behind;
  /** Repo-relative paths the reply named, first seen first. */
  paths: string[];
  excerpt: string;
  /** The error the server returned instead of a result. */
  error: string | null;
}

const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);
const rec = (v: unknown): Rec => (isRec(v) ? v : {});
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
/** A count: a number as sent, or a list's length. */
const count = (v: unknown): number | null => (Array.isArray(v) ? v.length : num(v));

function fact(what: string, n: number | null, of: number | null = null): ReplyFact[] {
  if (n === null) return [];
  return of !== null && of > n ? [{ what, n, of }] : [{ what, n }];
}

/** A sum over targets of one count, or null when no target carried it. */
function sumOver(targets: Rec[], read: (t: Rec) => number | null): number | null {
  const found = targets.map(read).filter((n): n is number => n !== null);
  return found.length === 0 ? null : found.reduce((a, b) => a + b, 0);
}

const targetsOf = (r: Rec): Rec[] => Object.values(rec(r["targets"])).filter(isRec);
const flagged = (targets: Rec[], key: string): number | null =>
  targets.some((t) => key in t) ? targets.filter((t) => t[key] === true).length : null;

function contextFacts(r: Rec): ReplyFact[] {
  const targets = targetsOf(r);
  const docs = targets.map((t) => rec(t["docs"]));
  const symbols = sumOver(docs, (d) => count(d["symbols"]));
  const symbolTotal = sumOver(docs, (d) => num(rec(d["symbols_truncated"])["total"]) ?? count(d["symbols"]));
  return [
    ...fact("targets", targets.length),
    ...fact("docs", docs.filter((d) => str(d["title"]) !== null).length || null),
    ...fact("symbols", symbols, symbolTotal),
    ...fact("callers", sumOver(targets, (t) => count(t["callers"]))),
    ...fact("callees", sumOver(targets, (t) => count(t["callees"]))),
    ...fact("decisions", sumOver(targets, (t) => count(t["decisions"]))),
    ...fact("hotspots", flagged(targets, "hotspot") || null),
  ];
}

function riskFacts(r: Rec): ReplyFact[] {
  const targets = targetsOf(r);
  return [
    ...fact("targets", targets.length),
    ...fact("dependents", sumOver(targets, (t) => num(t["dependents_count"]))),
    ...fact("coChange", sumOver(targets, (t) => num(t["co_change_partners_total"]) ?? count(t["co_change_partners"]))),
    ...fact("hotspots", flagged(targets, "is_hotspot") || null),
  ];
}

function answerFacts(r: Rec): ReplyFact[] {
  return [
    ...fact("citations", count(r["citations"])),
    ...fact("bodies", count(r["symbol_bodies"])),
    ...fact("rationale", count(r["code_rationale"]), num(r["code_rationale_total"])),
    ...fact("guesses", count(r["best_guesses"])),
  ];
}

function whyFacts(r: Rec): ReplyFact[] {
  const git = rec(r["git_archaeology"]);
  return [
    ...fact("decisions", count(r["decisions"])),
    ...fact("rationale", count(r["code_rationale"]), num(r["code_rationale_total"])),
    ...fact("commits", count(git["git_log"] ?? git["file_commits"])),
  ];
}

function searchFacts(r: Rec): ReplyFact[] {
  return fact("results", count(r["results"]));
}

function symbolFacts(r: Rec): ReplyFact[] {
  const start = num(r["start_line"]);
  const end = num(r["end_line"]);
  return [
    ...fact("lines", start !== null && end !== null && end >= start ? end - start + 1 : null),
    ...fact("callers", count(r["callers"])),
    ...fact("callees", count(r["callees"])),
  ];
}

/** The reply's own top-level lists, by key, for a tool without a reader. */
function listFacts(r: Rec): ReplyFact[] {
  return Object.entries(r)
    .filter(([key, v]) => Array.isArray(v) && v.length > 0 && !key.startsWith("_"))
    .slice(0, 3)
    .map(([key, v]) => ({ what: key, n: (v as unknown[]).length }));
}

const READERS: Record<string, (r: Rec) => ReplyFact[]> = {
  get_context: contextFacts,
  get_risk: riskFacts,
  get_answer: answerFacts,
  get_why: whyFacts,
  search_codebase: searchFacts,
  get_symbol: symbolFacts,
};

function insideOf(tool: string, r: Rec): ReplyFact[] {
  const known = READERS[tool]?.(r) ?? [];
  return known.length > 0 ? known : listFacts(r);
}

function behindOf(r: Rec, meta: Rec): Behind {
  const budget = rec(meta["response_budget"]);
  const used = num(budget["serialized_chars"]);
  const limit = num(budget["limit_chars"]);
  const completeness = rec(meta["completeness"]);
  const capped = bool(completeness["capped"]);
  return {
    commit: str(meta["indexed_commit"]),
    ageDays: num(meta["index_age_days"]),
    indexBehind: bool(meta["index_behind"]),
    verified: bool(r["verified"]),
    complete: bool(meta["complete"]) ?? (capped === null ? null : !capped),
    confidence: str(r["confidence"]),
    grounding: str(r["grounding"]),
    retrieval: str(r["retrieval_quality"]),
    budget: used !== null && limit !== null ? { used, limit } : null,
    omittedTokens: num(rec(meta["omitted"])["tokens"]),
    degraded: str(r["degraded"]) ?? str(meta["degraded"]),
    semantic: bool(meta["semantic_search"]),
  };
}

type Add = (p: unknown) => void;

/** Where a key holds paths: a path-like value, a list of cited files, the targets answered for. */
const PATH_READERS: Record<string, (value: unknown, add: Add) => void> = {
  path: (v, add) => add(v),
  file: (v, add) => add(v),
  file_path: (v, add) => add(v),
  citations: (v, add) => (Array.isArray(v) ? v.forEach(add) : undefined),
  targets: (v, add) => (isRec(v) ? Object.keys(v).forEach(add) : undefined),
};

function walkPaths(v: unknown, depth: number, add: Add): void {
  if (depth > MAX_DEPTH) return;
  if (Array.isArray(v)) return v.forEach((item) => walkPaths(item, depth + 1, add));
  for (const [key, value] of Object.entries(rec(v))) {
    if (key === "_meta") continue;
    PATH_READERS[key]?.(value, add);
    walkPaths(value, depth + 1, add);
  }
}

/** Paths the reply named, first seen first, at most MAX_PATHS. */
function pathsOf(r: Rec): string[] {
  const found = new Set<string>();
  walkPaths(r, 0, (p) => {
    if (typeof p === "string" && p !== "" && found.size < MAX_PATHS) found.add(p.replace(/\\/g, "/"));
  });
  return [...found];
}

const EMPTY_BEHIND: Behind = behindOf({}, {});

function utf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** The first line of an error, as the server wrote it. */
function errorOf(body: Rec): string | null {
  const err = body["error"];
  if (typeof err === "string") return err.split("\n")[0] ?? err;
  return str(rec(err)["message"]);
}

/** A reply already parsed, with its size when known. */
function summarizeBody(tool: string, body: unknown, bytes: number | null, excerpt = ""): ReplySummary {
  const r = rec(body);
  const meta = rec(r["_meta"]);
  return {
    tool,
    bytes,
    parsed: true,
    inside: insideOf(tool, r),
    behind: behindOf(r, meta),
    paths: pathsOf(r),
    excerpt,
    error: errorOf(r),
  };
}

/**
 * A reply as Claude received it: the MCP text (`{"result": {...}}`, its
 * `_meta` inside the result or beside it). Text that is not JSON, or too
 * large to parse here, keeps its size and excerpt only.
 */
export function summarizeReply(tool: string, text: string): ReplySummary {
  const bytes = utf8Bytes(text);
  const excerpt = text.slice(0, EXCERPT_CHARS);
  const parsed = text.length <= PARSE_CHARS ? parseObject(text) : null;
  if (parsed === null) return unparsed(tool, text, bytes, excerpt);
  const summary = summarizeBody(tool, resultOf(parsed), bytes, excerpt);
  return summary.error === null ? { ...summary, error: errorOf(parsed) } : summary;
}

/** The text as a JSON object, or null when it is not one. */
function parseObject(text: string): Rec | null {
  try {
    const v: unknown = JSON.parse(text);
    return isRec(v) ? v : null;
  } catch {
    // Not JSON: an error message, or a reply cut short. The caller keeps its first line.
    return null;
  }
}

/** The result object, with a `_meta` that came beside it moved inside. */
function resultOf(parsed: Rec): Rec {
  const result = isRec(parsed["result"]) ? parsed["result"] : parsed;
  return isRec(parsed["_meta"]) && !isRec(result["_meta"]) ? { ...result, _meta: parsed["_meta"] } : result;
}

/** A reply read by size alone; its first line is the error when short enough to be one. */
function unparsed(tool: string, text: string, bytes: number, excerpt: string): ReplySummary {
  const first = text.trim().split("\n")[0] ?? "";
  const error = text.length <= PARSE_CHARS && first !== "" ? first : null;
  return { tool, bytes, parsed: false, inside: [], behind: EMPTY_BEHIND, paths: [], excerpt, error };
}
