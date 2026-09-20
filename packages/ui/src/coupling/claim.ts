/**
 * The one place that turns a coupling edge into words and into a segment.
 *
 * `support` and the two directional confidences carry a claim a single
 * strength score cannot: a file that never changes without its partner is a
 * finding, while "they both change a lot" is not. Every surface that describes
 * a pair — table, panel, AI prompt — phrases it from here.
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
 * side no resolver could ever emit an edge for (a manifest, a changelog, a
 * file the parser never saw), where there was no edge to look for and so no
 * hidden coupling to claim.
 */
export type CouplingSegment = "unexplained" | "explained" | "outside";

/** The segment an edge belongs to, or `null` on an index written before the label existed. */
export function segmentOf(edge: CouplingEdge): CouplingSegment | null {
  if (edge.structural === "unexplained") return "unexplained";
  if (edge.structural === "corroborated") return "explained";
  if (edge.structural === "not_applicable") return "outside";
  return null;
}

/**
 * How the graph joins two files, as a noun phrase. Keyed on the wire's
 * `edge_type`, so an unrecognized kind reads as `null` and the caller falls
 * back to naming no kind at all rather than printing a raw identifier.
 */
const DEPENDENCY_KIND_WORDS: Record<string, string> = {
  imports: "An import",
  type_use: "A type reference",
  framework: "Framework wiring",
  dynamic_uses: "A dynamic reference",
  dynamic_imports: "A dynamic import",
  dynamic_url_route: "A URL route",
  reads: "A member read",
};

/**
 * The named dependency behind an explained pair, or `null` when there is none
 * to name: a different segment, an index written before the kind was
 * recorded, or a kind this table does not know.
 */
export function dependencyKindPhrase(edge: CouplingEdge): string | null {
  if (segmentOf(edge) !== "explained") return null;
  const kind = edge.dependency_kind;
  return kind ? (DEPENDENCY_KIND_WORDS[kind] ?? null) : null;
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
    case "explained": {
      // Naming the kind is the point: an import and a framework binding are
      // different explanations, and "explained" alone says neither.
      const phrase = dependencyKindPhrase(edge);
      return phrase
        ? `${phrase} in the graph already connects them.`
        : "The dependency graph already connects them.";
    }
    case "outside":
      return "At least one side cannot carry a dependency edge.";
    default:
      return "";
  }
}

/**
 * The pair's claim as a sentence, using *label* to name each file the way the
 * caller names it elsewhere.
 *
 * The asymmetry is the content: the side that rarely changes alone leads, and
 * the other side's independence follows. Every figure is one the wire carries;
 * no commit total is derived from a rounded share.
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
