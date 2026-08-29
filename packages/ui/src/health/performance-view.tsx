"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import type {
  PerformanceContextFilter,
  PerformanceOpportunity,
  PerformanceOpportunityPage,
} from "@repowise-dev/types/health";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { PageLede } from "../shared/page-lede";
import { PaginationControls } from "../shared/pagination-controls";
import { StatRibbon } from "../stats/stat-ribbon";
import { AiPromptModal } from "./ai-prompt-modal";
import {
  buildPerformanceOpportunityPrompt,
  buildRefactoringPlanPrompt,
} from "./ai-prompt-builder";
import type { PerformanceViewAdapter } from "./performance/adapter";
import { capabilitiesOf } from "./performance/capabilities";
import { OpportunityDrawer } from "./performance/drawer";
import { ContextHint, ContextTabs, QueueFilters, ScopeLine } from "./performance/filters";
import { LegacyPerformanceFindings } from "./performance/legacy";
import { OpportunityQueue } from "./performance/queue";
import {
  clearNarrowing,
  INITIAL_FILTERS,
  narrowingCount,
  parseFilters,
  serializeFilters,
  toQuery,
  withFilter,
  type NarrowingKey,
  type PerformanceFilterState,
} from "./performance/query";
import {
  EmptyQueue,
  FilteredEmpty,
  IgnoredArgumentsNotice,
  QueueError,
  QueueSkeleton,
  StaleModelNotice,
  UnavailableQueue,
} from "./performance/states";

/**
 * The Performance tab: which repeated cost is worth an intervention, why it
 * ranks where it does, and what evidence stands behind it.
 *
 * Composition and state only. Grouping, ranking, filtering, paging, facet
 * counts, and plan linkage are all decided by the server, so this surface and
 * the agent surface cannot disagree about what one opportunity is.
 */

export type { PerformanceViewAdapter };

const PAGE_SIZE = 20;

export function PerformanceView({
  adapter,
  initialFilters,
  onFiltersChange,
}: {
  adapter: PerformanceViewAdapter;
  /** A serialized filter state, so a shared link opens the same queue. */
  initialFilters?: string | undefined;
  /** Called with the serialized state whenever a filter moves. */
  onFiltersChange?: ((search: string) => void) | undefined;
}) {
  const [filters, setFilters] = useState<PerformanceFilterState>(() =>
    initialFilters ? parseFilters(initialFilters) : INITIAL_FILTERS,
  );
  const [selected, setSelected] = useState<PerformanceOpportunity | null>(null);
  // The handoff carries the verified plan when the drawer proved one, so a
  // plan-ready row hands over the ready payload rather than an instruction to
  // re-derive it.
  const [promptFor, setPromptFor] = useState<{
    opportunity: PerformanceOpportunity;
    plan: RefactoringPlan | null;
  } | null>(null);

  const apply = (next: PerformanceFilterState) => {
    setFilters(next);
    onFiltersChange?.(serializeFilters(next));
  };

  const load = adapter.getPerformanceOpportunities;
  // What a response proved this server understands. It is part of the cache key
  // so that learning it re-asks with the spelling the server accepts: a shared
  // link can restore a production or tooling context before any response has
  // been seen. It only ever flips for a server that predates the vocabulary, so
  // a current one never pays for the correction.
  const [collapse, setCollapse] = useState(false);
  const key = `${collapse ? "legacy:" : ""}${serializeFilters(filters)}`;
  const { data, error, isLoading, mutate } = useSWR<PerformanceOpportunityPage>(
    load ? `performance-opportunities:${adapter.cacheKey}:${key}` : null,
    () => load!(toQuery(filters, { limit: PAGE_SIZE, collapseContexts: collapse })),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  const capabilities = useMemo(() => capabilitiesOf(adapter, data), [adapter, data]);
  useEffect(() => {
    if (data) setCollapse(!capabilitiesOf(adapter, data).canonicalContexts);
  }, [adapter, data]);

  if (!load) return <LegacyPerformanceFindings adapter={adapter} />;
  if (error && [404, 405].includes(Number((error as { status?: number }).status))) {
    return <LegacyPerformanceFindings adapter={adapter} />;
  }
  if (error) return <QueueError onRetry={() => void mutate()} />;
  if (isLoading && !data) return <QueueSkeleton />;
  if (!data) return null;

  const { summary, facets } = data;
  const narrowed = narrowingCount(filters);
  const filtered = narrowed > 0 || filters.context !== "all";

  if (summary.status === "unavailable") return <UnavailableQueue summary={summary} />;

  const stats = [
    {
      label: "Plan ready",
      value: (summary.actionability?.plan_ready ?? 0).toLocaleString(),
      sub: "a named safe intervention",
    },
    {
      label: "Advisory",
      value: (summary.actionability?.advisory ?? 0).toLocaleString(),
      sub: "coherent, not proven safe",
    },
    {
      label: "Needs investigation",
      value: (summary.actionability?.investigate ?? 0).toLocaleString(),
      sub: "read the evidence first",
    },
    {
      label: "With a stored plan",
      value: summary.with_plan_total.toLocaleString(),
      sub: `of ${summary.total.toLocaleString()} causes`,
    },
  ];

  return (
    <div className="space-y-8">
      <div className="space-y-6">
        <PageLede
          label="Causal opportunities"
          value={summary.total.toLocaleString()}
          unit="ranked causes, not repeated observations"
          layout="beside"
          figureFooter={
            <p className="text-caption text-[var(--color-text-tertiary)]">
              Static, high-precision signals. They are not measured latency.
            </p>
          }
        >
          <p>
            Repeated caller paths fold into one cause, so the first row answers what to change
            once while every raw observation stays readable as evidence.
          </p>
          <p>
            Evidence confidence, actionability, and fix safety are three separate judgements. A
            path the analysis resolved well is not automatically a change worth making.
          </p>
        </PageLede>
        <StatRibbon stats={stats} />
      </div>

      <section className="space-y-4 border-t border-[var(--color-border-default)] pt-8">
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">
            Opportunity queue
          </h2>
          <p className="mt-1 max-w-[72ch] text-sm text-[var(--color-text-secondary)]">
            Ranked with the actionable work first. Filters and their counts come from the server,
            so narrowing one never hides the alternatives to it.
          </p>
        </div>

        {summary.status === "stale_model" ? <StaleModelNotice summary={summary} /> : null}
        {data.ignored_arguments ? (
          <IgnoredArgumentsNotice ignored={data.ignored_arguments} />
        ) : null}

        <ContextTabs
          value={filters.context}
          facets={facets}
          total={summary.total}
          collapsed={!capabilities.canonicalContexts}
          onChange={(context: PerformanceContextFilter) =>
            apply(withFilter(filters, "context", context))
          }
        />
        <ContextHint context={filters.context} />

        {capabilities.serverFacets ? (
          <QueueFilters
            filters={filters}
            facets={facets}
            onChange={(key: NarrowingKey, value) =>
              apply(withFilter(filters, key, value as never))
            }
            onClear={() => apply(clearNarrowing(filters))}
          />
        ) : null}

        <ScopeLine
          filteredTotal={data.total}
          repositoryTotal={summary.total}
          context={filters.context}
          narrowed={narrowed}
          analyzedCommit={summary.analyzed_commit}
        />

        {data.items.length === 0 ? (
          filtered ? (
            <FilteredEmpty onClear={() => apply({ ...clearNarrowing(filters), context: "all" })} />
          ) : (
            <EmptyQueue />
          )
        ) : (
          <OpportunityQueue
            items={data.items}
            facets={facets}
            adapter={adapter}
            showSections={filters.actionability === null}
            selectedId={selected?.opportunity_id ?? null}
            onInspect={setSelected}
          />
        )}

        <PaginationControls
          offset={filters.offset}
          shown={data.items.length}
          total={data.total}
          label="opportunities"
          onPrevious={
            filters.offset > 0
              ? () => apply(withFilter(filters, "offset", Math.max(0, filters.offset - PAGE_SIZE)))
              : undefined
          }
          onNext={
            data.next_offset != null
              ? () => apply(withFilter(filters, "offset", data.next_offset!))
              : undefined
          }
        />
      </section>

      <OpportunityDrawer
        opportunity={selected}
        adapter={adapter}
        detailEnabled={capabilities.detailById}
        planEnabled={capabilities.planById}
        onClose={() => setSelected(null)}
        onAgentHandoff={(opportunity, plan) => setPromptFor({ opportunity, plan })}
      />

      <AiPromptModal
        open={promptFor !== null}
        onOpenChange={(open) => {
          if (!open) setPromptFor(null);
        }}
        getPrompt={
          promptFor?.plan
            ? (flavor) => buildRefactoringPlanPrompt({ plan: promptFor.plan!, flavor })
            : promptFor
              ? (flavor) =>
                  buildPerformanceOpportunityPrompt({
                    opportunity: promptFor.opportunity,
                    flavor,
                  })
              : null
        }
        filePath={promptFor?.plan?.file_path ?? promptFor?.opportunity.file_path ?? null}
        title={promptFor?.plan ? "Structured plan handoff" : "Agent handoff"}
        description={
          promptFor?.plan
            ? "The stored plan for this exact opportunity, with its intervention, affected targets, and validation contract."
            : "A ready-to-paste evidence handoff that asks an agent to verify the cause against the source before proposing any edit."
        }
      />
    </div>
  );
}
