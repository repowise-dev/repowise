/**
 * What a commit or a change did to code health, read from its finding counts.
 * Pure, so the list cell, the detail lede and the prompt all agree.
 */

export type HealthDeltaKind = "improved" | "worsened" | "mixed" | "neutral";

/** The counts every health delta carries. `improved` is null until stored
 *  deltas carry the analyzer's improved bucket. A server rollup may also send
 *  its own `kind`, which wins over the one read from the counts. */
export interface HealthDeltaCounts {
  introduced: number;
  worsened: number;
  resolved: number;
  improved?: number | null | undefined;
  kind?: HealthDeltaKind | undefined;
  /** `partial` when part of the change was not compared. */
  status?: string | undefined;
  /** Written by an older analyzer: the direction holds, the counts do not compare. */
  stale?: boolean | undefined;
}

/** Findings the change made better: resolved, plus improved when known. */
export function betterCount(d: HealthDeltaCounts): number {
  return d.resolved + (d.improved ?? 0);
}

/** Findings the change made worse: new ones plus ones it made more severe. */
export function worseCount(d: HealthDeltaCounts): number {
  return d.introduced + d.worsened;
}

export function healthDeltaKind(d: HealthDeltaCounts): HealthDeltaKind {
  if (d.kind) return d.kind;
  const better = betterCount(d) > 0;
  const worse = worseCount(d) > 0;
  if (better && worse) return "mixed";
  if (better) return "improved";
  return worse ? "worsened" : "neutral";
}

/** Attribution bases where the change wrote the code a finding sits on. The
 *  rest only touched the file, a weaker claim that is labelled as one. */
export const DIRECT_ATTRIBUTION: ReadonlySet<string> = new Set(["added_lines", "changed_symbol", "new_file"]);

/** How a change relates to a finding: it wrote the line (`direct`), carried
 *  the code from another file (`moved`), edited a call that reaches it
 *  (`call_edge`), nothing in its diff explains it (`unexplained`), or it only
 *  touched the file (`touched`). */
export type AttributionKind = "direct" | "moved" | "call_edge" | "unexplained" | "touched";

export function attributionKind(f: {
  attribution_basis: string;
  moved_from?: { path: string } | null;
}): AttributionKind {
  if (f.attribution_basis === "moved" || f.moved_from) return "moved";
  if (DIRECT_ATTRIBUTION.has(f.attribution_basis)) return "direct";
  if (f.attribution_basis === "changed_call_edge") return "call_edge";
  if (f.attribution_basis === "unknown") return "unexplained";
  return "touched";
}

/** Where a moved finding's code came from (`symbol in path`), or null when not stored. */
export function movedFromLabel(f: { moved_from?: { path: string; symbol?: string | null } | null }): string | null {
  const src = f.moved_from;
  if (!src) return null;
  return src.symbol ? `${src.symbol} in ${src.path}` : src.path;
}

/** Findings grouped by file, files in order of their first (worst) finding. */
export function groupFindingsByFile<F extends { path: string }>(findings: F[]): { path: string; findings: F[] }[] {
  const groups = new Map<string, F[]>();
  for (const f of findings) {
    const group = groups.get(f.path);
    if (group) group.push(f);
    else groups.set(f.path, [f]);
  }
  return [...groups].map(([path, items]) => ({ path, findings: items }));
}
