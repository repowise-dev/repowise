"use client";

import { memo, useState } from "react";
import { Copy, ExternalLink, FileCode, MoreHorizontal } from "lucide-react";
import { toast } from "sonner";
import type {
  PerformanceActionabilityState,
  PerformanceFacets,
  PerformanceOpportunity,
} from "@repowise-dev/types/health";

import { Button } from "../../ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../../ui/popover";
import { CLICKABLE_ROW_CLS, clickableRowProps } from "../../shared/responsive-table";
import type { PerformanceViewAdapter } from "./adapter";
import {
  ACTIONABILITY_HINT,
  ACTIONABILITY_LABEL,
  affectedSummary,
  agentHandoffCall,
  boundaryLabel,
  contextLabel,
  CONFIDENCE_LABEL,
  opportunityEvidenceLine,
  opportunityTitle,
  planPresentation,
  whyRankedLabel,
} from "./presentation";

/**
 * The queue: full-width rows under section headings, one primary verb each.
 *
 * Sections are contiguous runs of the server's order, never a regrouping. The
 * server ranks actionable work first, so the runs read as tiers; if that order
 * ever changed, the runs would still describe exactly what was returned.
 */

interface Section {
  state: PerformanceActionabilityState;
  items: PerformanceOpportunity[];
}

export function contiguousSections(items: PerformanceOpportunity[]): Section[] {
  const sections: Section[] = [];
  for (const item of items) {
    const last = sections[sections.length - 1];
    if (last && last.state === item.actionability_state) last.items.push(item);
    else sections.push({ state: item.actionability_state, items: [item] });
  }
  return sections;
}

function facetTotal(facets: PerformanceFacets, value: string): number | null {
  const found = facets.actionability?.find((entry) => entry.value === value);
  return found ? found.total : null;
}

export function OpportunityQueue({
  items,
  facets,
  adapter,
  showSections,
  selectedId,
  onInspect,
}: {
  items: PerformanceOpportunity[];
  facets: PerformanceFacets;
  adapter: PerformanceViewAdapter;
  /** Sections are headings over one ranked list, so a narrowed queue drops them. */
  showSections: boolean;
  selectedId: string | null;
  onInspect: (opportunity: PerformanceOpportunity) => void;
}) {
  const sections = showSections
    ? contiguousSections(items)
    : [{ state: items[0]?.actionability_state ?? "investigate", items }];

  return (
    <div className="space-y-8">
      {sections.map((section, index) => (
        <section key={`${section.state}-${index}`} aria-labelledby={`perf-section-${index}`}>
          {showSections ? (
            <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h3
                id={`perf-section-${index}`}
                className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
              >
                {ACTIONABILITY_LABEL[section.state]}
                {facetTotal(facets, section.state) != null ? (
                  <span className="ml-2 tabular-nums">
                    {facetTotal(facets, section.state)!.toLocaleString()} in the repository
                  </span>
                ) : null}
              </h3>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                {ACTIONABILITY_HINT[section.state]}
              </p>
            </div>
          ) : (
            <h3 id={`perf-section-${index}`} className="sr-only">
              Performance opportunities
            </h3>
          )}
          <ul className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
            {section.items.map((opportunity) => (
              <OpportunityRow
                key={opportunity.opportunity_id}
                opportunity={opportunity}
                adapter={adapter}
                selected={opportunity.opportunity_id === selectedId}
                onInspect={onInspect}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function StatusMark({ opportunity }: { opportunity: PerformanceOpportunity }) {
  const plan = planPresentation(opportunity);
  const tone = plan.actionable ? "var(--color-success)" : "var(--color-text-tertiary)";
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium text-[var(--color-text-secondary)]">
      <span
        aria-hidden="true"
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: tone }}
      />
      {plan.label}
    </span>
  );
}

/**
 * Memoized because a page is twenty of these and the surrounding filter state
 * changes far more often than any single row does. It takes the callback
 * rather than a closure over one opportunity so the props stay comparable.
 */
const OpportunityRow = memo(function OpportunityRow({
  opportunity,
  adapter,
  selected,
  onInspect,
}: {
  opportunity: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
  selected: boolean;
  onInspect: (opportunity: PerformanceOpportunity) => void;
}) {
  const title = opportunityTitle(opportunity);
  const why = opportunity.why_ranked.slice(0, 3);
  return (
    <li
      data-performance-opportunity={opportunity.opportunity_id}
      aria-current={selected ? "true" : undefined}
      className={`${CLICKABLE_ROW_CLS} flex items-start gap-3 px-1 py-4 sm:gap-4 ${
        selected ? "bg-[var(--color-bg-elevated)]" : "hover:bg-[var(--color-bg-elevated)]"
      }`}
      aria-label={`Inspect ${title}`}
      {...clickableRowProps(() => onInspect(opportunity))}
    >
      <span className="mt-0.5 w-7 shrink-0 text-right font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {opportunity.rank_position.toLocaleString()}
      </span>

      <div className="min-w-0 flex-1">
        <p className="text-[15px] font-semibold leading-snug text-[var(--color-text-primary)]">
          {title}
        </p>
        <p className="mt-1 break-all font-mono text-xs text-[var(--color-text-secondary)]">
          {opportunityEvidenceLine(opportunity)}
        </p>
        <p className="mt-1.5 text-xs text-[var(--color-text-tertiary)]">
          {contextLabel(opportunity.execution_context)} ·{" "}
          {boundaryLabel(opportunity.boundary_kind)} ·{" "}
          <span className="tabular-nums">{affectedSummary(opportunity)}</span> ·{" "}
          {CONFIDENCE_LABEL[opportunity.confidence]} evidence confidence
        </p>
        {why.length > 0 ? (
          <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            Ranked on {why.map(whyRankedLabel).join(", ")}
          </p>
        ) : null}
        <div className="mt-2 sm:hidden">
          <StatusMark opportunity={opportunity} />
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1 sm:gap-3">
        <span className="hidden sm:inline">
          <StatusMark opportunity={opportunity} />
        </span>
        <RowOverflow opportunity={opportunity} adapter={adapter} title={title} />
      </div>
    </li>
  );
});

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied`);
  } catch {
    toast.error(`Could not copy the ${what.toLowerCase()}`);
  }
}

/**
 * Secondary actions. Inspect is the row itself, so nothing here repeats it and
 * each entry names a different kind of destination.
 */
function RowOverflow({
  opportunity,
  adapter,
  title,
}: {
  opportunity: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
  title: string;
}) {
  const [open, setOpen] = useState(false);
  const plan = planPresentation(opportunity);
  const planHref =
    plan.actionable && opportunity.plan_id
      ? adapter.refactoringPlanHref?.(opportunity.plan_id, opportunity.opportunity_id)
      : undefined;

  const item = (
    label: string,
    Icon: typeof Copy,
    onClick: () => void,
  ) => (
    <button
      type="button"
      onClick={() => {
        setOpen(false);
        onClick();
      }}
      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {label}
    </button>
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 w-7 px-0 text-[var(--color-text-tertiary)]"
          aria-label={`More actions for ${title}`}
          onClick={(event) => event.stopPropagation()}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-64 p-1"
        onClick={(event) => event.stopPropagation()}
      >
        {item("Open the file", FileCode, () =>
          adapter.navigate(adapter.fileHref(opportunity.file_path)),
        )}
        {planHref
          ? item("Open the plan on Refactoring", ExternalLink, () => adapter.navigate(planHref))
          : null}
        {item("Copy the agent drill-down", Copy, () =>
          copy(agentHandoffCall(opportunity.opportunity_id), "Drill-down call"),
        )}
        {item("Copy the opportunity id", Copy, () =>
          copy(opportunity.opportunity_id, "Opportunity id"),
        )}
      </PopoverContent>
    </Popover>
  );
}
