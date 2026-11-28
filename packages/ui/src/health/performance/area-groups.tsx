"use client";

import { memo, useState } from "react";
import useSWR from "swr";
import { ChevronRight } from "lucide-react";
import type { PerformanceOpportunity } from "@repowise-dev/types/health";

import { bulkAgentPromptLabel } from "../ai-prompt-action";
import { AiPromptButton } from "../ai-prompt-button";
import { hairlineCardClass } from "../../shared/hairline-card";
import type { PerformanceViewAdapter } from "./adapter";
import type { PerformanceAreaGroup } from "./grouping";
import { ACTIONABILITY_LABEL } from "./presentation";
import { OpportunityList, QUIET_LINK_CLS, type RowCallbacks } from "./queue";
import { formatNumber } from "../../lib/format";

/**
 * The queue grouped by area: one card per directory, the area with the most
 * plan-ready causes first, each showing its leading causes and a way to the
 * rest. A card is an object here, since it can be opened, collapsed and
 * handed to an agent as a batch.
 */

/** Causes a card lists before "Show all". */
export const AREA_PREVIEW = 3;

export function areaLabel(area: string): string {
  return area || "Repository root";
}

/** A card's heading: an area, or the opened area's own files. */
function cardLabel(group: PerformanceAreaGroup): string {
  if (!group.own) return areaLabel(group.area);
  return group.area ? `Files directly in ${group.area}` : "Files at the repository root";
}

/** Cards past this many never fetch their own rows, so a wide scope is not a burst. */
const FETCHING_CARDS = 8;

function countsLine(group: PerformanceAreaGroup): string {
  const parts = (["plan_ready", "advisory", "investigate", "expected"] as const)
    .filter((state) => group.counts[state] > 0)
    .map((state) => `${formatNumber(group.counts[state])} ${ACTIONABILITY_LABEL[state].toLowerCase()}`);
  return parts.join(" · ");
}

/**
 * The top causes of one area when the loaded page holds too few of them:
 * keyed by the query, so it is fetched once per area and view.
 */
export interface AreaTopRows {
  key: string;
  load: (area: string) => Promise<PerformanceOpportunity[]>;
}

export function AreaGroups({
  groups,
  adapter,
  selectedId,
  callbacks,
  onOpenArea,
  onAreaPrompt,
  preview = AREA_PREVIEW,
  topRows,
}: {
  groups: PerformanceAreaGroup[];
  /** Fetches an area's top causes when the loaded page lacks them. */
  topRows?: AreaTopRows | undefined;
  adapter: PerformanceViewAdapter;
  selectedId: string | null;
  callbacks: RowCallbacks;
  /** Open one area on its own, every cause listed. */
  onOpenArea: (area: string) => void;
  /** `partial` when the rows are only the area's leading causes. */
  onAreaPrompt: (area: string, items: PerformanceOpportunity[], partial: boolean) => void;
  /** Causes each card lists before "Show all"; `Infinity` lists every one. */
  preview?: number;
}) {
  return (
    <div className="space-y-4">
      {groups.map((group, index) => (
        <AreaCard
          key={`${group.own ? "own:" : ""}${group.area}`}
          group={group}
          mayFetch={index < FETCHING_CARDS}
          adapter={adapter}
          selectedId={selectedId}
          callbacks={callbacks}
          onOpenArea={onOpenArea}
          onAreaPrompt={onAreaPrompt}
          preview={preview}
          topRows={topRows}
        />
      ))}
    </div>
  );
}

const AreaCard = memo(function AreaCard({
  group,
  adapter,
  selectedId,
  callbacks,
  onOpenArea,
  onAreaPrompt,
  preview,
  topRows,
  mayFetch,
}: {
  group: PerformanceAreaGroup;
  topRows?: AreaTopRows | undefined;
  mayFetch: boolean;
  adapter: PerformanceViewAdapter;
  selectedId: string | null;
  callbacks: RowCallbacks;
  onOpenArea: (area: string) => void;
  /** `partial` when the rows are only the area's leading causes. */
  onAreaPrompt: (area: string, items: PerformanceOpportunity[], partial: boolean) => void;
  preview: number;
}) {
  const [open, setOpen] = useState(true);
  const label = cardLabel(group);
  // The loaded page may hold none of a small area's causes; ask for them.
  // An own-files card cannot be asked for by area, which would list its subareas too.
  const short = group.items.length === 0 && group.total > 0 && !group.own && mayFetch;
  const { data: fetched } = useSWR<PerformanceOpportunity[]>(
    open && short && topRows ? `performance-area-top:${topRows.key}:${group.area}` : null,
    () => topRows!.load(group.area),
    { revalidateOnFocus: false },
  );
  const rows = short && fetched ? fetched : group.items;
  const shown = rows.slice(0, preview);
  const rest = group.total - shown.length;
  return (
    <section
      aria-label={`${label}, ${group.total} causes`}
      className={hairlineCardClass("group")}
    >
      <header className="flex flex-col items-start gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-3">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded text-left sm:w-auto sm:flex-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          <ChevronRight
            aria-hidden="true"
            className={`h-4 w-4 shrink-0 text-[var(--color-text-tertiary)] transition-transform ${open ? "rotate-90" : ""}`}
          />
          <span className="min-w-0 font-mono text-sm font-medium text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
            {label}
          </span>
          <span className="shrink-0 font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
            {countsLine(group)}
          </span>
        </button>
        {/* Ghost: the tab has no scope-level prompt, so no card claims the
            one filled prompt; the drawer holds it for a single cause. */}
        <AiPromptButton
          variant="ghost"
          label={bulkAgentPromptLabel(rows.length)}
          ariaLabel={`${bulkAgentPromptLabel(rows.length)} in ${label}`}
          onClick={() => onAreaPrompt(group.area, rows, rows.length < group.total)}
          className="-mr-2 whitespace-nowrap"
        />
      </header>
      {open ? (
        <div className="px-3 pb-3">
          <OpportunityList
            items={shown}
            adapter={adapter}
            selectedId={selectedId}
            callbacks={callbacks}
            label={`Causes in ${label}`}
          />
          {rest > 0 && !group.own ? (
            <button
              type="button"
              onClick={() => onOpenArea(group.area)}
              className={`${QUIET_LINK_CLS} mt-2 px-1 text-xs`}
            >
              Show all {formatNumber(group.total)} in {label}
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
});
