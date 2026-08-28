/** Tab ids for the file entity page, their copy, and the badge rules.
 *
 *  Lives outside the client component so server components can validate
 *  `?tab=` values, build the tab row and render the panels without importing
 *  client code. Order is the rendered tab order: Overview first so undocumented
 *  files are never empty; Coverage last (often empty).
 */

import type { FileDetailResponse } from "@repowise-dev/types/files";

export const FILE_PAGE_TABS = [
  "overview",
  "doc",
  "health",
  "history",
  "decisions",
  "graph",
  "coverage",
] as const;
export type FilePageTab = (typeof FILE_PAGE_TABS)[number];

/** Tab names. "Documentation" rather than "Doc" per the fixed vocabulary —
 *  name things the way a person would. "Dependencies" rather than "Graph"
 *  because the tab is the file's neighbourhood in the dependency graph, not a
 *  canvas; the lists inside it are "Depended on by" / "Depends on" so the tab
 *  name and a column heading never read as the same thing. */
export const FILE_TAB_LABEL: Record<FilePageTab, string> = {
  overview: "Overview",
  doc: "Documentation",
  health: "Health",
  history: "History",
  decisions: "Decisions",
  graph: "Dependencies",
  coverage: "Tests",
};

/**
 * The sentence under the tab row. Says what the tab measures rather than
 * repeating its own name, which the tab row already carries.
 *
 * Each one has to stay true above the panel's *empty* state, because it renders
 * either way. "The documentation page Repowise wrote for this file" sitting
 * directly on top of "No documentation page for this file" asserts the thing
 * exists and is then contradicted a line later, so these are all phrased as
 * what the tab is for rather than as a claim that the data landed.
 */
export const FILE_TAB_BLURB: Record<FilePageTab, string> = {
  overview: "The shape of the file: what it holds, what it touches, and what needs attention.",
  doc: "What Repowise has written about this file.",
  health: "Defect risk, the biomarkers deducted from it, and how each function churns.",
  history: "What git knows about this file — how often it changes, who changes it, and what moves with it.",
  decisions: "Architectural decisions recorded against this file.",
  graph: "Where this file sits in the indexed dependency graph.",
  coverage:
    "Which tests reach this file, read from the dependency graph, plus the lines they executed once a coverage report has been ingested.",
};

export interface FileTabDef {
  id: FilePageTab;
  label: string;
  /** How much is behind the tab, so a clean one says so before it is clicked. */
  badge?: number | string;
  blurb: string;
}

/**
 * The tab row for a file.
 *
 * Every badge here is already in the aggregate, so none of them costs a call —
 * the Code Health rule is "only show the ones you can afford", and these are
 * free. They are also only rendered where there is something to say: a file
 * with no findings and no coverage carries no figures, per rule 10.
 *
 * Decisions is the one tab that disappears. A file governed by nothing has an
 * empty tab that can never fill from this page, where every other tab either
 * has data or explains how to get it.
 */
export function fileTabsFor(data: FileDetailResponse): FileTabDef[] {
  const findings = data.health.findings.length;
  const decisions = data.governing_decisions?.length ?? 0;
  // Same fallback the header's stat ribbon uses. Reading only `coverage` here
  // left a file whose percentage rides on the health metric showing "Coverage
  // 61%" in the ribbon above an unbadged Coverage tab — one number, two
  // sources, which is rule 20's second corollary.
  const coveragePct = data.coverage?.line_coverage_pct ?? data.health.metric?.line_coverage_pct;

  return FILE_PAGE_TABS.filter((id) => id !== "decisions" || decisions > 0).map((id) => {
    const badge =
      id === "health" && findings > 0
        ? findings
        : id === "decisions" && decisions > 0
          ? decisions
          : id === "coverage" && coveragePct != null
            ? `${Math.round(coveragePct)}%`
            : undefined;
    return {
      id,
      label: FILE_TAB_LABEL[id],
      blurb: FILE_TAB_BLURB[id],
      ...(badge !== undefined ? { badge } : {}),
    };
  });
}

/** Narrow a `?tab=` string to a tab this page actually renders.
 *
 *  Lives here rather than beside `FilePage`: that module is `"use client"`, so
 *  every export of it is a client reference, and a server component that
 *  imported this one got "it's not possible to invoke a client function from
 *  the server" instead of a tab id. */
export function asFilePageTab(value: string | undefined): FilePageTab | undefined {
  return value && (FILE_PAGE_TABS as readonly string[]).includes(value)
    ? (value as FilePageTab)
    : undefined;
}

/** The tab a `?tab=` value resolves to, given what this file actually has.
 *  A deep link to Decisions on an ungoverned file lands on Overview rather
 *  than on a tab that is not in the row. */
export function resolveFileTab(
  requested: string | undefined,
  tabs: FileTabDef[],
): FilePageTab {
  const match = tabs.find((t) => t.id === requested);
  return match ? match.id : "overview";
}
