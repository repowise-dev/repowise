"use client";

import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import type { PerformanceOpportunity } from "@repowise-dev/types/health";

import { ActiveFilterChips, type ActiveFilterChip } from "../shared/more-filters";
import { PaginationControls } from "../shared/pagination-controls";
import { Button } from "../ui/button";
import { useAiPromptAction } from "./ai-prompt-action";
import { ActionabilityBar } from "./performance/actionability-bar";
import type { PerformanceViewAdapter } from "./performance/adapter";
import {
  AREA_PREVIEW,
  AreaGroups,
  areaLabel,
  type AreaTopRows,
} from "./performance/area-groups";
import { useOpportunityDetail, usePrefetchDetail } from "./performance/cache";
import { NarrowingFilters, narrowingChipLabel, SortControl } from "./performance/filters";
import {
  areaGroups,
  countsFromFacet,
  excludedReviews,
  facetAreaGroups,
  type ExcludedReview,
} from "./performance/grouping";
import { LegacyPerformanceFindings } from "./performance/legacy";
import { ACTIONABILITY_LABEL } from "./performance/presentation";
import { usePerformancePrompts } from "./performance/prompts";
import {
  clearNarrowing,
  NARROWING_KEYS,
  narrowingCount,
  narrowingKeysFor,
  PAGE_SIZE,
  readFilters,
  serverKey,
  toQuery,
  withFilter,
  withScope,
  writeFilters,
  type PerformanceFilterState,
  type PerformanceScope,
} from "./performance/query";
import { QUIET_LINK_CLS, type RowCallbacks } from "./performance/queue";
import { ScopeControl, ScopeSentence, type ScopeCounts } from "./performance/scope";
import {
  FilteredEmpty,
  GoodState,
  IgnoredArgumentsNotice,
  LinkedCauseUnavailable,
  QueueError,
  QueueSkeleton,
  StaleModelNotice,
  UnavailableQueue,
} from "./performance/states";
import { usePerformanceFilters } from "./performance/use-filters";
import { usePerformanceQueue } from "./performance/use-queue";
import { PerformanceVerdict, verdictOf } from "./performance/verdict";

/**
 * The Performance tab: how many changes are worth making now, where they sit,
 * and a prompt for an agent to make each one.
 *
 * One scope (worth doing now, all production, everything) decides which
 * causes exist, and every figure is counted inside it by the server: the
 * lede, the actionability bar and the filter counts. Grouping by area is a
 * pure derivation over the loaded page (`performance/grouping`).
 */

export type { PerformanceViewAdapter, PerformanceFilterState };
export { readFilters as readPerformanceFilters, writeFilters as writePerformanceFilters };

const OpportunityDrawer = lazy(() => import("./performance/drawer"));

/** How the good state names each scope. */
const GOOD_STATE_SCOPE: Record<PerformanceScope, string> = {
  now: "worth making now in production code",
  production: "found in production code",
  all: "found in any code",
};

export function PerformanceView({
  adapter,
  initialFilters,
  onFiltersChange,
  openOpportunityId,
  onOpenOpportunityChange,
}: {
  adapter: PerformanceViewAdapter;
  /** The tab's URL parameters (or a pre-flat `perf` blob), so a shared link opens the same view. */
  initialFilters?: string | undefined;
  /** Called on every change with the flat parameters and the state they encode. */
  onFiltersChange?: ((search: string, state: PerformanceFilterState) => void) | undefined;
  /**
   * A cause to open on arrival, by its stable id. A link from the map or the
   * file drawer names one, and it need not be on the loaded page, so it is
   * resolved by id rather than looked for in the queue.
   */
  openOpportunityId?: string | null;
  /** Called with the open cause's id, or null when the drawer closes. */
  onOpenOpportunityChange?: ((opportunityId: string | null) => void) | undefined;
}) {
  const { filters, current, apply } = usePerformanceFilters(initialFilters, onFiltersChange);

  const { data, error, isLoading, mutate, capabilities, supported } = usePerformanceQueue(
    adapter,
    filters,
  );

  // The open cause. A click holds the row so the drawer paints at once; a
  // link or a sibling names an id, resolved through the same detail key.
  const [selected, setSelected] = useState<PerformanceOpportunity | null>(null);
  const [internalOpenId, setInternalOpenId] = useState<string | null>(null);
  const select = useCallback(
    (next: PerformanceOpportunity | null) => {
      setSelected(next);
      setInternalOpenId(null);
      onOpenOpportunityChange?.(next?.opportunity_id ?? null);
    },
    [onOpenOpportunityChange],
  );
  const openById = useCallback(
    (opportunityId: string) => {
      setInternalOpenId(opportunityId);
      onOpenOpportunityChange?.(opportunityId);
    },
    [onOpenOpportunityChange],
  );
  const effectiveOpenId = openOpportunityId ?? internalOpenId;
  const pendingId =
    effectiveOpenId && effectiveOpenId !== selected?.opportunity_id ? effectiveOpenId : null;
  const { data: linked, error: linkError } = useOpportunityDetail(
    adapter,
    capabilities.detailById ? pendingId : null,
  );
  useEffect(() => {
    // An id from a retired model resolves to a state, not a cause; opening a
    // drawer on it would render an empty panel.
    if (linked?.found) setSelected(linked);
  }, [linked]);

  const { openPrompt, promptModal } = useAiPromptAction();
  const prompts = usePerformancePrompts(adapter);
  const prefetch = usePrefetchDetail(adapter);
  const detailById = capabilities.detailById;
  const callbacks: RowCallbacks = useMemo(
    () => ({
      onInspect: select,
      onPrompt: (opportunity) => openPrompt(prompts.forOpportunity(opportunity)),
      onPrefetch: (id) => {
        if (detailById) prefetch(id);
      },
    }),
    [select, openPrompt, prompts, prefetch, detailById],
  );
  const onAreaPrompt = useCallback(
    (area: string, rows: PerformanceOpportunity[], partial: boolean) =>
      openPrompt(prompts.forArea(area, rows, partial)),
    [openPrompt, prompts],
  );
  const onOpenArea = useCallback(
    (area: string) => apply(withFilter(current.current, "area", area)),
    [apply],
  );

  const items = useMemo(() => data?.items ?? [], [data]);
  const counts = useMemo(() => countsFromFacet(data?.facets.actionability), [data]);
  // Every area in the scope with its true counts, from the server's facet; a
  // server that predates it groups the loaded page instead.
  const groups = useMemo(
    () => (data?.areas ? facetAreaGroups(items, data.areas) : areaGroups(items)),
    [data, items],
  );
  const focused = filters.area !== null;
  // An opened area that does not split further lists every loaded cause in it.
  const listAll = focused && groups.length <= 1;
  // A small area's causes may sit past the loaded page; a card asks for its top.
  // Page-independent: the area facet and each card's top do not move with it.
  const viewKey = serverKey({ ...filters, page: 1 });
  const load = adapter.getPerformanceOpportunities;
  const topRows: AreaTopRows | undefined = useMemo(
    () =>
      load
        ? {
            key: `${adapter.cacheKey}:${viewKey}`,
            load: async (area: string) =>
              (
                await load(
                  toQuery(withFilter(current.current, "area", area), { limit: AREA_PREVIEW }),
                )
              ).items,
          }
        : undefined,
    [load, adapter.cacheKey, viewKey, current],
  );

  if (!supported) return <LegacyPerformanceFindings adapter={adapter} />;
  if (error && [404, 405].includes(Number((error as { status?: number }).status))) {
    return <LegacyPerformanceFindings adapter={adapter} />;
  }
  if (error) return <QueueError onRetry={() => void mutate()} />;
  if (isLoading && !data) return <QueueSkeleton />;
  if (!data) return null;

  const { summary, facets } = data;
  if (summary.status === "unavailable") return <UnavailableQueue summary={summary} />;

  // A server that predates context scoping sends no repository_total, and
  // one that predates scopes no scope sizes; both fall back to the summary.
  const repositoryTotal = summary.repository_total ?? summary.total;
  const scopes: ScopeCounts = {
    now: data.scopes?.now ?? summary.default_queue?.total ?? 0,
    production: data.scopes?.production ?? summary.context?.production ?? 0,
    all: data.scopes?.all ?? repositoryTotal,
  };
  const reviews = excludedReviews(summary.default_queue?.excluded);
  const toScope = (scope: PerformanceScope) => apply(withScope(filters, scope));
  const onReview = ({ patch: { scope, ...rest } }: ExcludedReview) =>
    apply(withScope(filters, scope, rest));
  const sentence = (
    <ScopeSentence
      scope={filters.scope}
      counts={scopes}
      reviews={reviews}
      onScope={toScope}
      onReview={onReview}
    />
  );
  const otherProduction = scopes.production - scopes.now;

  const chips: ActiveFilterChip[] = [
    ...NARROWING_KEYS.flatMap((key) => {
      const value = filters[key];
      return value
        ? [
            {
              key,
              label: narrowingChipLabel(key, value),
              onRemove: () => apply(withFilter(filters, key, null)),
            },
          ]
        : [];
    }),
    ...(filters.actionability
      ? [
          {
            key: "actionability",
            label: `Showing: ${ACTIONABILITY_LABEL[filters.actionability]}`,
            onRemove: () => apply(withFilter(filters, "actionability", null)),
          },
        ]
      : []),
    ...(filters.area !== null
      ? [
          {
            key: "area",
            label: `Area: ${areaLabel(filters.area)}`,
            onRemove: () => apply(withFilter(filters, "area", null)),
          },
        ]
      : []),
  ];

  const selectedId = selected?.opportunity_id ?? null;

  return (
    <div className="space-y-8">
      {items.length > 0 ? (
        <PerformanceVerdict
          verdict={verdictOf(filters.scope, data.total, groups)}
          analyzedCommit={summary.analyzed_commit}
        >
          {sentence}
        </PerformanceVerdict>
      ) : null}

      <section aria-label="Performance causes" className="space-y-4">
        {summary.status === "stale_model" ? <StaleModelNotice summary={summary} /> : null}
        {data.ignored_arguments ? (
          <IgnoredArgumentsNotice ignored={data.ignored_arguments} />
        ) : null}

        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
          <ScopeControl value={filters.scope} counts={scopes} onChange={toScope} />
          <SortControl
            value={filters.sort}
            onChange={(sort) => apply(withFilter(filters, "sort", sort))}
          />
        </div>
        {capabilities.serverFacets ? (
          <NarrowingFilters
            filters={filters}
            facets={facets}
            keys={narrowingKeysFor(filters.scope)}
            onChange={(key, value) => apply(withFilter(filters, key, value as never))}
          />
        ) : null}
        <ActiveFilterChips chips={chips} onClearAll={() => apply(clearNarrowing(filters))} />

        {items.length > 0 ? (
          <ActionabilityBar
            counts={counts}
            value={filters.actionability}
            onChange={(state) => apply(withFilter(filters, "actionability", state))}
          />
        ) : null}

        {items.length === 0 ? (
          // A scope with no causes is clean, not a failed filter.
          narrowingCount(filters) > 0 && repositoryTotal > 0 ? (
            <FilteredEmpty onClear={() => apply(clearNarrowing(filters))} />
          ) : (
            <GoodState
              scope={GOOD_STATE_SCOPE[filters.scope]}
              analyzedCommit={summary.analyzed_commit}
            >
              {sentence}
            </GoodState>
          )
        ) : (
          <div className="space-y-3">
            {focused ? (
              <button
                type="button"
                onClick={() => apply(withFilter(filters, "area", null))}
                className={`${QUIET_LINK_CLS} text-xs`}
              >
                Back to all areas
              </button>
            ) : null}
            <AreaGroups
              groups={groups}
              adapter={adapter}
              selectedId={selectedId}
              callbacks={callbacks}
              onOpenArea={onOpenArea}
              onAreaPrompt={onAreaPrompt}
              preview={listAll ? Number.POSITIVE_INFINITY : AREA_PREVIEW}
              topRows={topRows}
            />
            {data.areas && data.areas.more > 0 ? (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                {data.areas.more.toLocaleString()} more{" "}
                {data.areas.more === 1 ? "cause sits" : "causes sit"} in smaller areas not
                listed here.
              </p>
            ) : null}
          </div>
        )}

        {filters.scope === "now" && items.length > 0 && otherProduction > 0 ? (
          <Button variant="outline" size="sm" onClick={() => toScope("production")}>
            Show the {otherProduction.toLocaleString()} other production{" "}
            {otherProduction === 1 ? "cause" : "causes"}
          </Button>
        ) : null}

        {data.total > PAGE_SIZE ? (
          <PaginationControls
            offset={(filters.page - 1) * PAGE_SIZE}
            shown={items.length}
            total={data.total}
            label="causes"
            onPrevious={
              filters.page > 1
                ? () => apply(withFilter(filters, "page", filters.page - 1))
                : undefined
            }
            onNext={
              data.next_offset != null
                ? () => apply(withFilter(filters, "page", filters.page + 1))
                : undefined
            }
          />
        ) : null}
      </section>

      {pendingId && (linkError || linked?.found === false) ? (
        <LinkedCauseUnavailable
          opportunityId={pendingId}
          detail={linked && !linked.found ? linked.detail : null}
          onDismiss={() => onOpenOpportunityChange?.(null)}
        />
      ) : null}

      {selected ? (
        <Suspense fallback={null}>
          <OpportunityDrawer
            opportunity={selected}
            adapter={adapter}
            detailEnabled={detailById}
            planEnabled={capabilities.planById}
            onClose={() => select(null)}
            onAgentPrompt={callbacks.onPrompt}
            onSelectById={openById}
          />
        </Suspense>
      ) : null}

      {promptModal}
    </div>
  );
}
