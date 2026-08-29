/**
 * The one place that turns a coupling edge into words and into a segment.
 *
 * Both the table and the AI prompt used to restate the numbers their own way,
 * and the diagram had no way to say what a pair meant at all. `support` plus
 * the two directional confidences carry a claim the old `strength 4.16` never
 * could — that a file has never changed without its partner is a finding,
 * while "they both change a lot" is not — so the sentence is built once here
 * and reused wherever a pair is described.
 */
import type { CouplingEdge } from "@repowise-dev/types/coupling";

/** An unordered pair of files, as the wire orders them (source < target). */
export interface CouplingPair {
  source: string;
  target: string;
}

/** Whether *edge* is the pair described by *pair*. */
export function isSamePair(edge: CouplingEdge, pair: CouplingPair | null | undefined): boolean {
  return pair != null && edge.source === pair.source && edge.target === pair.target;
}

/** Whether *path* is one of the pair's two ends. */
export function pairHas(pair: CouplingPair | null | undefined, path: string | null): boolean {
  return pair != null && path != null && (pair.source === path || pair.target === path);
}

/**
 * The reader-facing split of the list, derived from the wire's three-valued
 * `structural`. `unexplained` is the finding; `explained` is a pair the
 * dependency graph already accounts for; `outside` is a pair with at least one
 * side the parser never ingested (a lockfile, a changelog), where there was no
 * edge to look for and so no hidden coupling to claim.
 */
export type CouplingSegment = "unexplained" | "explained" | "outside";

/** The segment an edge belongs to, or `null` on an index written before the label existed. */
export function segmentOf(edge: CouplingEdge): CouplingSegment | null {
  if (edge.structural === "unexplained") return "unexplained";
  if (edge.structural === "corroborated") return "explained";
  if (edge.structural === "not_applicable") return "outside";
  return null;
}

/** The higher of the two directions: the strongest claim the pair supports. */
export function peakConfidence(edge: CouplingEdge): number | null {
  const values = [edge.confidence_ab, edge.confidence_ba].filter(
    (v): v is number => typeof v === "number",
  );
  return values.length ? values.reduce((m, v) => Math.max(m, v), 0) : null;
}

function pct(value: number): number {
  return Math.round(value * 100);
}

/**
 * What the dependency graph says, as a clause. Empty for an older index that
 * carries no label — silence is honest there, a guess is not.
 */
export function structuralClause(edge: CouplingEdge): string {
  switch (segmentOf(edge)) {
    case "unexplained":
      return "No dependency in the graph connects them.";
    case "explained":
      return "The dependency graph already connects them.";
    case "outside":
      return "At least one side is outside the dependency graph.";
    default:
      return "";
  }
}

/**
 * The pair's claim as a sentence, using *label* to name each file the way the
 * caller names it elsewhere (the disambiguated basename in the table, the full
 * path in a prompt).
 *
 * The asymmetry is the content: the side that rarely changes alone leads, and
 * the other side's independence is stated as the share of its own commits that
 * did *not* touch the first. Neither number is a derived commit total — only
 * the shares the wire carries and the shared-commit count behind them.
 */
export function couplingClaim(
  edge: CouplingEdge,
  label: (path: string) => string = (p) => p,
): string {
  const clause = structuralClause(edge);
  const withClause = (lead: string) => (clause ? `${lead} ${clause}` : lead);

  const ab = edge.confidence_ab;
  const ba = edge.confidence_ba;
  const support = edge.support;

  if (!support) {
    return withClause("The index recorded no shared-commit count for this pair.");
  }

  const shared = `${support} shared ${support === 1 ? "commit" : "commits"}`;

  if (typeof ab === "number" && typeof ba === "number") {
    // Lead with whichever side is the more dependent — that is the finding.
    const abLeads = ab >= ba;
    const hi = abLeads ? edge.source : edge.target;
    const lo = abLeads ? edge.target : edge.source;
    const hiConf = abLeads ? ab : ba;
    const loConf = abLeads ? ba : ab;
    const alone = 100 - pct(loConf);
    const head =
      pct(hiConf) >= 100
        ? `${label(hi)} has never changed without ${label(lo)} (${shared})`
        : `${label(hi)} changed with ${label(lo)} in ${pct(hiConf)}% of its own commits (${shared})`;
    const tail =
      alone <= 0
        ? `and ${label(lo)} never changed without it either`
        : `while ${label(lo)} changed without it ${alone}% of the time`;
    return withClause(`${head}, ${tail}.`);
  }

  const only =
    typeof ab === "number"
      ? { self: edge.source, partner: edge.target, conf: ab }
      : typeof ba === "number"
        ? { self: edge.target, partner: edge.source, conf: ba }
        : null;

  if (only) {
    return withClause(
      `${label(only.self)} changed with ${label(only.partner)} in ${pct(only.conf)}% of its own commits (${shared}).`,
    );
  }

  return withClause(`${shared} touched both files.`);
}
