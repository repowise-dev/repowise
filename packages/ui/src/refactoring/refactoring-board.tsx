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
 * **The list defaults to what Fix first would take.** Every per-file
 * opportunity is a lot of rows (702 on this repository), most of them below
 * the worth floor or in tests. Core's Fix-first rules decide which are worth
 * doing; the scope control switches to the full inventory, and the count line
 * says how many the default leaves out and why.
 *
 * Filters are server-owned and single-valued, which is what the queue endpoint
 * admits: one effort, one confidence. Chips toggle rather than accumulate, and
 * the confidence row is built from the confidences actually present, so it
 * disappears on a repo whose opportunities are all one confidence.
 */

import * as React from "react";
import { Search } from "lucide-react";

import { EmptyState } from "../shared/empty-state";

import { Input } from "../ui/input";
import { FilterSelect } from "../health/code-health-controls";
import { PaginationControls } from "../shared/pagination-controls";
import { Segmented } from "../shared/segmented";
import { exclusionPhrase } from "../health/fix-first/scope";
import { formatNumber } from "../lib/format";
import { OpportunityRows } from "./opportunity-rows";
import { RefactoringLede, type RefactoringFacets } from "./refactoring-lede";
import { StartHere } from "./start-here";
import { CONFIDENCE_LABEL, EFFORT_LABEL } from "./meta";
import { STATUS_LABEL, TRIAGE_STATUSES } from "./opportunity";
import type {
  Confidence,
  EffortBucket,
  OpportunityStatus,
  RefactoringOpportunity,
  RefactoringHiddenCounts,
  RefactoringOpportunityRollup,
  RefactoringOrder,
  RefactoringScope,
} from "@repowise-dev/types/refactoring";

const PAGE_SIZE = 60;

const EFFORTS: EffortBucket[] = ["S", "M", "L", "XL"];
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
  /** The scope asked for: what Fix first would take, or the full inventory. */
  scope?: RefactoringScope | undefined;
  /**
   * The scope the server applied, which `total` counts. Omit when the host has
   * no scoped listing: the Scope control is not shown and the list is the full
   * inventory.
   */
  appliedScope?: RefactoringScope | undefined;
  /** Under `fix_first`, what the filtered set leaves out, by reason. */
  hidden?: RefactoringHiddenCounts | null | undefined;
  total: number;
  offset: number;
  nextOffset: number | null;
}

export interface RefactoringBoardProps {
  /** Opportunities for the active type filter, in the server's order. */
  opportunities: RefactoringOpportunity[];
  /** The repository rollup the endpoint returns. Feeds the lede. */
  summary?: RefactoringOpportunityRollup | null | undefined;
  /** The page's facet counts, which the lede's filter chips count. */
  facets?: RefactoringFacets | null | undefined;
  /** Bounded structural head for Start here, already filtered to lead types. */
  structuralOpportunities?: RefactoringOpportunity[] | undefined;
  serverState: RefactoringBoardServerState;
  onServerStateChange: (change: Partial<RefactoringBoardServerState>) => void;
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
  /** Shown with the all-clear when the analysis ran and found nothing. */
  emptyClearHint?: string;
}

export function RefactoringBoard({
  opportunities,
  summary,
  facets,
  structuralOpportunities,
  serverState,
  onServerStateChange,
  onOpen,
  onAiPrompt,
  onStatusChange,
  fileHref,
  onSeeStructural,
  showLede = true,
  sectionTitle = "All opportunities",
  emptyTitle = "No refactoring opportunities yet",
  emptyHint = "Opportunities appear here when a file is worth splitting, a cycle worth cutting, a class worth extracting, or a long function worth breaking up.",
  emptyClearHint = "No file is worth splitting, no cycle worth cutting and no class worth extracting across this repository.",
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
  // Empty only when the inventory is: the default scope can list none of it.
  const inventory = rollupTotal ?? serverState.total + (serverState.hidden?.total ?? 0);
  const filtersActive =
    serverState.query.trim() !== "" ||
    serverState.effort !== null ||
    serverState.confidence !== null ||
    serverState.mechanicalOnly ||
    serverState.status !== "open";
  if (inventory === 0 && !filtersActive) {
    const analysed = summary?.status === "available";
    return (
      <div className="space-y-10">
        {showLede ? <RefactoringLede summary={summary} indexedFileCount={indexedFileCount} /> : null}
        <EmptyState
          tone={analysed ? "positive" : "neutral"}
          title={analysed ? "No refactoring targets" : emptyTitle}
          description={analysed ? emptyClearHint : emptyHint}
        />
      </div>
    );
  }

  const clearFilters = () =>
    onServerStateChange({
      query: "",
      status: "open",
      effort: null,
      confidence: null,
      mechanicalOnly: false,
      offset: 0,
    });
  const resultTotal = serverState.total;
  const worthOnly = serverState.appliedScope === "fix_first";
  const hiddenTotal = serverState.hidden?.total ?? 0;
  const hiddenWhy = serverState.hidden ? exclusionPhrase(serverState.hidden.by_reason) : "";

  const quickWinsActive = serverState.effort === "S";
  const toggleQuickWins = () =>
    onServerStateChange({ effort: quickWinsActive ? null : "S", offset: 0 });

  return (
    <div className="space-y-10">
      {showLede ? (
        <RefactoringLede
          summary={summary}
          facets={facets}
          quickWinsActive={quickWinsActive}
          onToggleQuickWins={toggleQuickWins}
          onSeeStructural={onSeeStructural}
        />
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
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">
              {sectionTitle}
            </h2>
            <p className="mt-1 max-w-[68ch] text-sm text-[var(--color-text-secondary)]">
              One row is one file&apos;s work, with its steps in dependency-safe order. The
              recommended order rotates cause and area so the head is a set of choices; every row
              opens the same inspector with the full explanation.
            </p>
          </div>
          {serverState.appliedScope ? (
          <Segmented<RefactoringScope>
            label="Scope"
            value={serverState.appliedScope}
            onChange={(scope) => onServerStateChange({ scope, offset: 0 })}
            options={[
              {
                value: "fix_first",
                label: "Worth doing",
                hint: "Only what Fix first would take",
                disabledReason:
                  serverState.status === "open"
                    ? undefined
                    : "Fix first ranks open opportunities only.",
              },
              { value: "all", label: "Full inventory", hint: "Every open opportunity" },
            ]}
          />
          ) : null}
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
              className="font-mono text-caption uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
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
              ...EFFORTS.map((e) => ({ value: e, label: EFFORT_LABEL[e] })),
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

        <div className="flex items-start justify-between gap-4">
          {worthOnly ? (
            <div>
              <h3 className="text-sm font-semibold tabular-nums text-[var(--color-text-primary)]">
                Showing {formatNumber(resultTotal)} worth doing
                {filtersActive ? (
                  <span className="font-normal text-[var(--color-text-tertiary)]"> that match</span>
                ) : null}
                ; {formatNumber(hiddenTotal)} more in the full inventory
              </h3>
              {hiddenWhy ? (
                <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
                  Left out: {hiddenWhy}.
                </p>
              ) : null}
            </div>
          ) : (
            <h3 className="text-sm font-semibold tabular-nums text-[var(--color-text-primary)]">
              {resultTotal.toLocaleString()} {STATUS_LABEL[serverState.status].toLowerCase()}{" "}
              opportunit{resultTotal === 1 ? "y" : "ies"}
              {filtersActive ? (
                <span className="font-normal text-[var(--color-text-tertiary)]"> matching</span>
              ) : null}
            </h3>
          )}
          {filtersActive ? (
            <button
              type="button"
              onClick={clearFilters}
              className="text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-text-primary)] hover:underline"
            >
              Clear filters
            </button>
          ) : null}
        </div>

        {opportunities.length === 0 ? (
          <EmptyState
            tone="filtered"
            title={
              worthOnly && hiddenTotal > 0
                ? `None of these is worth doing first. ${formatNumber(hiddenTotal)} more are in the full inventory.`
                : serverState.status === "open"
                  ? "No opportunities match these filters"
                  : `Nothing has been marked ${STATUS_LABEL[serverState.status].toLowerCase()} yet`
            }
            {...(filtersActive ? { action: { label: "Clear filters", onClick: clearFilters } } : {})}
          />
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
          ? "border-[var(--color-border-hover)] bg-[var(--color-bg-selected)] text-[var(--color-text-primary)]"
          : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)]"
      }`}
    >
      {label}
    </button>
  );
}
