import * as React from "react";
import type {
  DecisionGraph,
  DecisionGraphNode,
} from "@repowise-dev/types/decisions";

/**
 * What the decision graph actually knew, as lists.
 *
 * The page used to close with a React Flow canvas of the same payload. It lost
 * on its *data*, the way the churn scatter did:
 *
 *   - 373 of 376 decision edges were `supersedes` and 3 were `conflicts_with`.
 *     `refines` and `relates_to` — two of the four kinds the canvas styled —
 *     never occurred at all, so it was a one-relation diagram.
 *   - Two thirds of the nodes had no decision edge whatsoever, and rendered as
 *     a field of isolated boxes.
 *   - Of 387 distinct edge endpoints, only 66 were in the node payload. The
 *     rest pointed at decisions the canvas could not draw.
 *   - It carried 9,221 code links and dropped 6,400 of them to a per-decision
 *     cap of 8, silently, on top of a 300-node layout ceiling.
 *
 * A supersession is lineage, which is linear — `DecisionLineage` already
 * renders it properly on the detail page. That leaves two things worth
 * surfacing, and both are lists: the handful of genuine conflicts, and the
 * files that the most decisions govern, which is the question you actually ask
 * before touching code.
 *
 * The aggregation runs on the server so the 9,221-edge payload never reaches
 * the browser. It used to be downloaded in full, to draw a diagram that threw
 * away two thirds of it.
 */

export interface DecisionConflict {
  aId: string;
  /** Undefined when the id resolves to nothing the caller could name. */
  aTitle?: string | undefined;
  bId: string;
  bTitle?: string | undefined;
  /** Why the pair was flagged. Auto-detected pairs carry a similarity note. */
  evidence?: string | undefined;
}

export interface GovernedFile {
  path: string;
  decisionCount: number;
}

export interface DecisionGovernanceSummary {
  conflicts: DecisionConflict[];
  governedFiles: GovernedFile[];
  /** Distinct files with at least one governing decision. */
  governedFileTotal: number;
}

/**
 * Reduces a `DecisionGraph` to the two lists worth rendering. Pure, so the
 * server component can call it directly and ship only the result.
 */
export function summarizeGovernance(
  graph: DecisionGraph | undefined,
  options: {
    topFiles?: number;
    /**
     * Extra id -> title pairs. Conflict endpoints routinely fall outside the
     * node payload — on a live index only 66 of 387 endpoints were in it — so
     * the caller supplies whatever else it has loaded and resolves the rest.
     */
    titles?: ReadonlyMap<string, string> | undefined;
  } = {},
): DecisionGovernanceSummary {
  const empty: DecisionGovernanceSummary = {
    conflicts: [],
    governedFiles: [],
    governedFileTotal: 0,
  };
  if (!graph) return empty;

  const titleById = new Map<string, string>(
    (graph.nodes ?? []).map((n: DecisionGraphNode) => [n.id, n.title]),
  );
  for (const [id, title] of options.titles ?? []) titleById.set(id, title);

  const seen = new Set<string>();
  const conflicts: DecisionConflict[] = [];
  for (const e of graph.decision_edges ?? []) {
    if (e.kind !== "conflicts_with") continue;
    // A conflict is symmetric; key on the unordered pair so it lists once.
    const key = [e.src, e.dst].sort().join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    conflicts.push({
      aId: e.src,
      aTitle: titleById.get(e.src),
      bId: e.dst,
      bTitle: titleById.get(e.dst),
      evidence: e.evidence ?? undefined,
    });
  }

  const perFile = new Map<string, Set<string>>();
  for (const e of graph.code_edges ?? []) {
    if (e.link_type !== "file") continue;
    const set = perFile.get(e.node_id) ?? new Set<string>();
    set.add(e.decision_id);
    perFile.set(e.node_id, set);
  }

  const governedFiles = [...perFile.entries()]
    .map(([path, ids]) => ({ path, decisionCount: ids.size }))
    .sort((a, b) => b.decisionCount - a.decisionCount || a.path.localeCompare(b.path))
    .slice(0, options.topFiles ?? 12);

  return { conflicts, governedFiles, governedFileTotal: perFile.size };
}

const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

type LinkComponent = React.ElementType<{
  href: string;
  className?: string;
  children: React.ReactNode;
}>;

export interface DecisionConflictsProps {
  conflicts: DecisionConflict[];
  decisionHref: (id: string) => string;
  LinkComponent?: LinkComponent;
}

/**
 * The only genuinely relational thing the graph carried, and the only one that
 * needs acting on. Renders nothing when there are none — rule 10: a quiet page
 * is a healthy page.
 */
export function DecisionConflicts({
  conflicts,
  decisionHref,
  LinkComponent = "a",
}: DecisionConflictsProps) {
  // A conflict we cannot name is not worth a row. Printing the id renders a
  // 32-character hash where a sentence should be, which tells the reader
  // nothing and cannot be acted on — the fallback that shipped in the first
  // cut of this component.
  const named = conflicts.filter((c) => c.aTitle && c.bTitle);
  if (named.length === 0) return null;
  const Link = LinkComponent;

  return (
    <ul className="border-t border-[var(--color-border-default)]">
      {named.map((c) => (
        <li
          key={`${c.aId}|${c.bId}`}
          className="border-b border-[var(--color-border-default)] py-3"
        >
          <div className="flex flex-col gap-1 text-sm sm:flex-row sm:items-baseline sm:gap-2">
            <Link
              href={decisionHref(c.aId)}
              className="font-medium text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)] hover:underline"
            >
              {c.aTitle}
            </Link>
            <span className={MICRO_LABEL}>conflicts with</span>
            <Link
              href={decisionHref(c.bId)}
              className="font-medium text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)] hover:underline"
            >
              {c.bTitle}
            </Link>
          </div>
          {c.evidence && (
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              {c.evidence}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

export interface GovernedFilesProps {
  files: GovernedFile[];
  fileHref?: (path: string) => string;
  LinkComponent?: LinkComponent;
}

/**
 * Which files carry the most decisions. This is the question the graph's
 * 9,221 code links could answer and its canvas could not, having dropped 69%
 * of them to stay laid out.
 */
export function GovernedFiles({
  files,
  fileHref,
  LinkComponent = "a",
}: GovernedFilesProps) {
  if (files.length === 0) return null;
  const Link = LinkComponent;
  const max = files[0]?.decisionCount ?? 1;

  return (
    <ul className="border-t border-[var(--color-border-default)]">
      {files.map((f) => (
        <li
          key={f.path}
          className="flex items-center gap-4 border-b border-[var(--color-border-default)] py-2.5"
        >
          <span className="min-w-0 flex-1">
            {fileHref ? (
              <Link
                href={fileHref(f.path)}
                className="font-mono text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:underline"
              >
                {f.path}
              </Link>
            ) : (
              <span className="font-mono text-xs text-[var(--color-text-secondary)]">
                {f.path}
              </span>
            )}
          </span>
          {/* Proportion bar steps the accent down toward the inset ground
              rather than reaching for a second hue. */}
          <span
            aria-hidden
            className="hidden h-1 w-24 shrink-0 overflow-hidden rounded-full bg-[var(--color-bg-inset)] sm:block"
          >
            <span
              className="block h-full rounded-full bg-[var(--color-accent-primary)]"
              style={{ width: `${Math.round((f.decisionCount / max) * 100)}%` }}
            />
          </span>
          <span className="w-16 shrink-0 text-right font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">
            {f.decisionCount}
          </span>
        </li>
      ))}
    </ul>
  );
}
