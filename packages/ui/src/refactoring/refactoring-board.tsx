"use client";

/**
 * The Refactoring surface.
 *
 * Lede, then the structural opportunities as a field with the top few ranked
 * under it, then every opportunity as hairline rows. What used to be here was a
 * priority-by-effort quadrant over a grid of cards; both were replaced for
 * reasons recorded in `structural-map.tsx` and `opportunity-rows.tsx`.
 *
 * **The list's default order is the diversified queue, not worst-files-first.**
 * Both are indexed columns, so the choice costs nothing either way and is
 * purely about the question the page answers. This page answers "what should I
 * do next", and the honest rank order puts eight interchangeable rows at the
 * head: single high-confidence extractions recovering the same quantised
 * `complex_method` deduction, tied to four decimal places and separated only by
 * file path. Diversification breaks that tie by rotating lead cause, lead type
 * and area, so the head reads as a set of choices rather than one choice typed
 * out eight times. "Worst files first" is a different question - where is the
 * damage, not what is cheap to fix - and Code Health's galaxy already answers
 * it, so it stays available here as a named order rather than becoming the
 * default and quietly making this a second, worse copy of that page.
 *
 * Filters are server-owned and single-valued, which is what the queue endpoint
 * admits: one effort, one confidence. Chips toggle rather than accumulate, and
 * the confidence row is built from the confidences actually present, so it
 * disappears on a repo whose opportunities are all one confidence.
 */

import * as React from "react";
import { Search } from "lucide-react";

import { Input } from "../ui/input";
import { FilterSelect } from "../health/code-health-controls";
import { PaginationControls } from "../shared/pagination-controls";
import { OpportunityRows } from "./opportunity-rows";
import { RefactoringLede } from "./refactoring-lede";
import { StartHere } from "./start-here";
import { CONFIDENCE_LABEL } from "./meta";
import { STATUS_LABEL, TRIAGE_STATUSES } from "./opportunity";
import type {
  Confidence,
  EffortBucket,
  OpportunityStatus,
  RefactoringOpportunity,
  RefactoringOpportunityRollup,
  RefactoringOrder,
} from "@repowise-dev/types/refactoring";

const PAGE_SIZE = 60;

const EFFORTS: EffortBucket[] = ["S", "M", "L", "XL"];
const EFFORT_LABEL_LONG: Record<EffortBucket, string> = {
  S: "Small",
  M: "Medium",
  L: "Large",
  XL: "Extra large",
};
const CONFIDENCE_ORDER: Confidence[] = ["high", "medium", "low"];

const SORT_OPTIONS: { value: RefactoringOrder; label: string }[] = [
  { value: "queue", label: "Recommended" },
  { value: "rank", label: "Highest value" },
  { value: "health", label: "Worst files first" },
  { value: "effort", label: "Effort, small first" },
  { value: "file", label: "File, A to Z" },
];

export interface RefactoringBoardServerState {
  query: string;
  order: RefactoringOrder;
  /** Which triage state the list is showing. The server defaults to `open`. */
  status: OpportunityStatus;
  effort: EffortBucket | null;
  confidence: Confidence | null;
  mechanicalOnly: boolean;
  total: number;
  offset: number;
  nextOffset: number | null;
}

export interface RefactoringBoardProps {
  /** Opportunities for the active type filter, in the server's order. */
  opportunities: RefactoringOpportunity[];
  /** The repository rollup the endpoint returns. Feeds the lede. */
  summary?: RefactoringOpportunityRollup | null | undefined;
  /** Bounded structural head for Start here, already filtered to lead types. */
  structuralOpportunities?: RefactoringOpportunity[] | undefined;
  serverState: RefactoringBoardServerState;
  onServerStateChange: (change: Partial<RefactoringBoardServerState>) => void;
  indexedFileCount?: number | undefined;
  onOpen?: ((opportunity: RefactoringOpportunity) => void) | undefined;
  onAiPrompt?: ((opportunity: RefactoringOpportunity) => void) | undefined;
  onStatusChange?:
    | ((
        opportunity: RefactoringOpportunity,
        status: OpportunityStatus,
      ) => Promise<void> | void)
    | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  /** Jump the type filter to the structural set. */
  onSeeStructural?: (() => void) | undefined;
  /** Hide the lede and Start here - for hosts that render their own header. */
  showLede?: boolean;
  sectionTitle?: string;
  emptyTitle?: string;
  emptyHint?: string;
}

export function RefactoringBoard({
  opportunities,
  summary,
  structuralOpportunities,
  serverState,
  onServerStateChange,
  indexedFileCount,
  onOpen,
  onAiPrompt,
  onStatusChange,
  fileHref,
  onSeeStructural,
  showLede = true,
  sectionTitle = "All opportunities",
  emptyTitle = "No refactoring opportunities",
  emptyHint = "Opportunities appear here when a file is worth splitting, a cycle worth cutting, a class worth extracting, or a long function worth breaking up.",
}: RefactoringBoardProps) {
  const [highlighted, setHighlighted] = React.useState<string | null>(null);

  // Only offer confidences that occur. A filter is worth building where there
  // is something to subtract from.
  const confidencesPresent = React.useMemo(() => {
    const present = new Set(opportunities.map((o) => o.confidence));
    const active = serverState.confidence;
    return CONFIDENCE_ORDER.filter((c) => present.has(c) || c === active);
  }, [opportunities, serverState.confidence]);

  const rollupTotal =
    summary && summary.status === "available" ? summary.opportunities_total : null;
  if ((rollupTotal ?? opportunities.length) === 0 && serverState.status === "open") {
    return (
      <div className="border-t border-[var(--color-border-default)] pt-10 text-center">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{emptyTitle}</h3>
        <p className="mx-auto mt-1.5 max-w-[56ch] text-sm text-[var(--color-text-tertiary)]">
          {emptyHint}
        </p>
      </div>
    );
  }

  const filtersActive =
    serverState.query.trim() !== "" ||
    serverState.effort !== null ||
    serverState.confidence !== null ||
    serverState.mechanicalOnly ||
    serverState.status !== "open";
  const resultTotal = serverState.total;

  return (
    <div className="space-y-10">
      {showLede ? (
        <RefactoringLede summary={summary} indexedFileCount={indexedFileCount} />
      ) : null}

      {showLede && (structuralOpportunities?.length ?? 0) > 0 ? (
        <StartHere
          opportunities={structuralOpportunities ?? []}
          onOpen={onOpen}
          onSeeAll={onSeeStructural}
          highlightedId={highlighted}
          onHighlight={setHighlighted}
        />
      ) : null}

      <section className="space-y-4 border-t border-[var(--color-border-default)] pt-8">
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">{sectionTitle}</h2>
          <p className="mt-1 max-w-[68ch] text-sm text-[var(--color-text-secondary)]">
            One row is one file&apos;s work, with its steps in dependency-safe order. The
            recommended order rotates cause and area so the head is a set of choices; every row
            opens the same inspector with the full explanation.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
            <Input
              value={serverState.query}
              onChange={(e) => onServerStateChange({ query: e.target.value, offset: 0 })}
              placeholder="Search by file path"
              className="pl-9"
              aria-label="Search opportunities by file path"
            />
          </div>
          <div className="flex items-center gap-2">
            <label
              htmlFor="refactoring-sort"
              className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
            >
              Sort
            </label>
            <select
              id="refactoring-sort"
              value={serverState.order}
              onChange={(e) =>
                onServerStateChange({ order: e.target.value as RefactoringOrder, offset: 0 })
              }
              className="h-9 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 text-sm text-[var(--color-text-primary)] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-accent-primary)]"
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {/* Selects, not chip rows. Status, effort and confidence are each a
              single choice over a closed set, which is what a select is for -
              three rows of chips spent a third of the page saying so, and a
              chip row implies multi-select to anyone who has used one. The
              mechanical filter stays a chip because it is a boolean, not a
              choice among values. */}
          <FilterSelect
            label="Status"
            value={serverState.status}
            onChange={(v) =>
              onServerStateChange({ status: v as OpportunityStatus, offset: 0 })
            }
            options={TRIAGE_STATUSES.map((o) => ({ value: o.value, label: o.label }))}
          />
          <FilterSelect
            label="Effort"
            value={serverState.effort ?? ""}
            onChange={(v) =>
              onServerStateChange({ effort: (v || null) as EffortBucket | null, offset: 0 })
            }
            options={[
              { value: "", label: "Any" },
              ...EFFORTS.map((e) => ({ value: e, label: EFFORT_LABEL_LONG[e] })),
            ]}
          />
          {confidencesPresent.length > 1 ? (
            <FilterSelect
              label="Confidence"
              value={serverState.confidence ?? ""}
              onChange={(v) =>
                onServerStateChange({ confidence: (v || null) as Confidence | null, offset: 0 })
              }
              options={[
                { value: "", label: "Any" },
                ...confidencesPresent.map((c) => ({ value: c, label: CONFIDENCE_LABEL[c] })),
              ]}
            />
          ) : null}
          <FilterChip
            active={serverState.mechanicalOnly}
            onClick={() =>
              onServerStateChange({ mechanicalOnly: !serverState.mechanicalOnly, offset: 0 })
            }
            label="Has a mechanical step"
          />
        </div>

        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold tabular-nums text-[var(--color-text-primary)]">
            {resultTotal.toLocaleString()} {STATUS_LABEL[serverState.status].toLowerCase()}{" "}
            opportunit{resultTotal === 1 ? "y" : "ies"}
            {filtersActive ? (
              <span className="font-normal text-[var(--color-text-tertiary)]"> matching</span>
            ) : null}
          </h3>
          {filtersActive ? (
            <button
              type="button"
              onClick={() =>
                onServerStateChange({
                  query: "",
                  status: "open",
                  effort: null,
                  confidence: null,
                  mechanicalOnly: false,
                  offset: 0,
                })
              }
              className="text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-text-primary)] hover:underline"
            >
              Clear filters
            </button>
          ) : null}
        </div>

        {opportunities.length === 0 ? (
          <p className="border-t border-[var(--color-border-default)] py-10 text-center text-sm text-[var(--color-text-tertiary)]">
            {serverState.status === "open"
              ? "No opportunities match these filters."
              : `Nothing has been marked ${STATUS_LABEL[serverState.status].toLowerCase()} yet.`}
          </p>
        ) : (
          <>
            <OpportunityRows
              opportunities={opportunities}
              onOpen={onOpen}
              onAiPrompt={onAiPrompt}
              onStatusChange={onStatusChange}
              fileHref={fileHref}
              highlightedId={highlighted}
              onHighlight={setHighlighted}
            />
            <PaginationControls
              offset={serverState.offset}
              shown={opportunities.length}
              total={serverState.total}
              label="opportunities"
              onPrevious={
                serverState.offset > 0
                  ? () =>
                      onServerStateChange({
                        offset: Math.max(0, serverState.offset - PAGE_SIZE),
                      })
                  : undefined
              }
              onNext={
                serverState.nextOffset != null
                  ? () => onServerStateChange({ offset: serverState.nextOffset! })
                  : undefined
              }
            />
          </>
        )}
      </section>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors ${
        active
          ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
          : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)]"
      }`}
    >
      {label}
    </button>
  );
}
