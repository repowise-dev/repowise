"use client";

/**
 * Findings — the triage list. One view at three levels: files ranked by
 * leverage, expandable to their findings, each finding naming its line and
 * linking to a plan where one exists. The function-level and hidden-coupling
 * panels sit below it; performance has its own causal-opportunity view.
 *
 * Presentation + orchestration only: the host injects data, links, and the
 * file-detail drawer through a {@link CodeHealthAdapter}.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { Search } from "lucide-react";
import type {
  HealthDimension,
  HealthFinding,
  HealthOverviewResponse,
  HealthWorkQueueQuery,
  HealthWorkQueueResponse,
  ImpactEffortQuery,
  ImpactEffortResponse,
} from "@repowise-dev/types/health";

import { Skeleton } from "../ui/skeleton";
import { Button } from "../ui/button";
import { EmptyState } from "../shared/empty-state";
import { ApiError } from "../shared/api-error";
import { toFriendlyMessage } from "../lib/errors";

import { AiPromptModal, fileChatContext } from "./ai-prompt-modal";
import { HotFunctionsPanel } from "./hot-functions-panel";
import { HiddenCouplingList } from "./hidden-coupling-list";
import {
  HealthWorkQueueList,
  type FindingStatus,
  type HealthWorkItem,
} from "./refactoring-target-list";
import type { HealthWorkItemFinding } from "./refactoring-card";
import { FilterSelect, FilterChip } from "./code-health-controls";
import { ImpactEffortQuadrant } from "./impact-effort-quadrant";
import { BulkTriageBar } from "./bulk-triage-bar";
import { usePrefersReducedMotion } from "../hooks/use-prefers-reduced-motion";
import {
  biomarkerLabel,
  DIMENSION_LABEL,
  HISTORY_EXPLAINER,
  HISTORY_LABEL,
  isWatchOnlyBiomarker,
} from "./biomarker-glossary";
import { buildAiPrompt } from "./ai-prompt-builder";
import { SEVERITY_LABEL, type Severity } from "./tokens";
import type { CodeHealthAdapter } from "./code-health-adapter";

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

type GroupBy = "none" | "biomarker" | "module" | "effort";

/** Function-level biomarker types fed to the Hot functions panel. */
const HOT_FN_TYPES = ["function_hotspot", "code_age_volatility", "complex_conditional"];

/** Worst first, matching how the list is read. */
const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];

/**
 * The floor of an exact selection, sent beside it so a host whose backend
 * knows only the threshold still narrows rather than showing everything.
 */
function lowestSeverity(picked: Severity[]): Severity {
  return SEVERITIES.filter((s) => picked.includes(s)).pop() ?? "low";
}

const EFFORT_LABEL: Record<string, string> = {
  S: "Small (≤40 NLOC)",
  M: "Medium (≤150 NLOC)",
  L: "Large (≤400 NLOC)",
  XL: "Extra large (>400 NLOC)",
};

export function FindingsView({ adapter }: { adapter: CodeHealthAdapter }) {
  const { cacheKey } = adapter;

  // Shares the page-level overview key, so this dedupes onto the one request
  // the landing view already fired — no extra round-trip. Used to gate the
  // queue fetch and to seed the marker filter with the repo's whole
  // vocabulary rather than only the markers the current page happens to hold.
  const { data: overview } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${cacheKey}`,
    () => adapter.getOverview(25),
    { revalidateOnFocus: false },
  );

  // The function-level panels and the hidden-coupling list pull four biomarker
  // types — fetched in ONE request (comma-separated `biomarker_type`) and
  // partitioned client-side, instead of four round-trips.
  const { data: panelFindings } = useSWR<HealthFinding[]>(
    `code-health-panel-findings:${cacheKey}`,
    () =>
      adapter
        .listFindings({
          biomarker_type: [...HOT_FN_TYPES, "hidden_coupling"].join(","),
          limit: 400,
        })
        .catch(() => [] as HealthFinding[]),
    { revalidateOnFocus: false },
  );

  const hotFunctionFindings = useMemo(
    () => panelFindings?.filter((f) => HOT_FN_TYPES.includes(f.biomarker_type)),
    [panelFindings],
  );
  const couplingFindings = useMemo(
    () => panelFindings?.filter((f) => f.biomarker_type === "hidden_coupling"),
    [panelFindings],
  );

  // ---- Filters ----
  // Every one of these narrows the same server-side query, so the counts and
  // the ranking beside the list always describe the rows in it. The page-level
  // Counts control owns the code-shape / everything reading; repeating it here
  // would put a second answer beside the first.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [dimension, setDimension] = useState<HealthDimension | "all">("all");
  const [severities, setSeverities] = useState<Severity[]>([]);
  const [biomarker, setBiomarker] = useState<string>("all");
  const [maxEffort, setMaxEffort] = useState<string>("all");
  const [onlyHotspots, setOnlyHotspots] = useState(false);
  const [onlyUntested, setOnlyUntested] = useState(false);
  const [onlyFailing, setOnlyFailing] = useState(false);
  const [sort, setSort] = useState<HealthWorkQueueQuery["sort"]>("impact_per_effort");
  const [groupBy, setGroupBy] = useState<GroupBy>("none");
  const [offset, setOffset] = useState(0);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [promptTarget, setPromptTarget] = useState<HealthWorkItem | null>(null);
  // History-only files are context, not work: out of the queue by default,
  // counted beside it, one toggle away.
  const [history, setHistory] = useState<"exclude" | "include">("exclude");
  // The file picked on the graph: highlighted in the list and scrolled to.
  const [highlightedPath, setHighlightedPath] = useState<string | null>(null);
  // Bulk triage: file path to the id of the finding the row names.
  const [selection, setSelection] = useState<ReadonlyMap<string, string>>(new Map());
  const reducedMotion = usePrefersReducedMotion();

  // Typing a path should not mint a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput);
      setOffset(0);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchInput]);

  /** Any narrowing beyond the defaults, which is what "clear" would undo. */
  const filtered =
    search !== "" ||
    dimension !== "all" ||
    severities.length > 0 ||
    biomarker !== "all" ||
    maxEffort !== "all" ||
    onlyHotspots ||
    onlyUntested ||
    onlyFailing;

  const clearFilters = () => {
    setSearchInput("");
    setSearch("");
    setDimension("all");
    setSeverities([]);
    setBiomarker("all");
    setMaxEffort("all");
    setOnlyHotspots(false);
    setOnlyUntested(false);
    setOnlyFailing(false);
    setOffset(0);
  };

  /** A filter change is a new question; page 1 is where it gets answered. */
  const onFilterChange =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      setOffset(0);
    };

  const toggleSeverity = (s: Severity) => {
    setSeverities((cur) => (cur.includes(s) ? cur.filter((v) => v !== s) : [...cur, s]));
    setOffset(0);
  };

  // The targets list no longer ships `all_findings` — it cost 1.8 MB per
  // request to serve two click-gated consumers. Both fetch here instead.
  // `limit` is generous: this is one file's findings, and the worst file in a
  // large repo carries a few hundred.
  const loadFindings = async (filePath: string) =>
    (await adapter.listFindings({
      file_path: filePath,
      limit: 1000,
      // The same narrowing the row's own count was computed under. Without it
      // "Show all 2 findings" opens onto fourteen.
      ...(dimension !== "all" && { dimension }),
      ...(biomarker !== "all" && { biomarker_type: biomarker }),
      ...(severities.length > 0 && {
        severity: severities.join(","),
        min_severity: lowestSeverity(severities),
      }),
    })) as HealthWorkItemFinding[];

  // Undefined when this host cannot answer, so the card stays silent rather
  // than rendering "no plan" for a question it never asked.
  const loadOpportunity = adapter.getFileOpportunity
    ? (filePath: string) => adapter.getFileOpportunity!(filePath)
    : undefined;

  // The prompt promises the agent "every marker", so hydrate before opening
  // rather than letting the builder fall back to the primary finding alone.
  const openPrompt = async (t: HealthWorkItem) => {
    setPromptTarget(t);
    try {
      setPromptTarget({ ...t, all_findings: await loadFindings(t.file_path) });
    } catch {
      // Leave the un-hydrated target in place: the builder degrades to the
      // primary finding, which is better than no prompt at all.
    }
  };

  const query: HealthWorkQueueQuery = useMemo(
    () => ({
      limit: PAGE_SIZE,
      offset,
      ...(search && { search }),
      ...(dimension !== "all" && { dimension }),
      ...(severities.length > 0 && {
        severity: severities.join(","),
        // A host whose backend predates the exact filter still narrows,
        // rather than showing an unfiltered list under an active chip.
        min_severity: lowestSeverity(severities),
      }),
      ...(biomarker !== "all" && { biomarker }),
      ...(maxEffort !== "all" && { max_effort: maxEffort }),
      ...(onlyHotspots && { only_hotspots: true }),
      ...(onlyUntested && { only_untested: true }),
      ...(onlyFailing && { only_failing: true }),
      ...(sort && { sort }),
      ...(history === "include" && { history }),
    }),
    [
      offset,
      history,
      search,
      dimension,
      severities,
      biomarker,
      maxEffort,
      onlyHotspots,
      onlyUntested,
      onlyFailing,
      sort,
    ],
  );

  const loadHealthWorkQueue = (opts: HealthWorkQueueQuery) => {
    const load = adapter.getHealthWorkQueue ?? adapter.getRefactoringTargets;
    return load ? load(opts) : Promise.resolve({ targets: [], total: 0 });
  };
  const {
    data: queue,
    isLoading: queueLoading,
    isValidating: queueValidating,
    error: queueError,
    mutate: mutateQueue,
  } = useSWR<HealthWorkQueueResponse>(
    overview ? `code-health-queue:${cacheKey}:${JSON.stringify(query)}` : null,
    () => loadHealthWorkQueue(query),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  // The graph takes the queue's filters without its paging, order or history
  // switch: it is the whole filtered set, and history-only files never plot.
  const planeQuery: ImpactEffortQuery = useMemo(() => {
    const { limit: _l, offset: _o, sort: _s, history: _h, ...rest } = query;
    return rest;
  }, [query]);
  const planeKey = JSON.stringify(planeQuery);
  const { data: plane, mutate: mutatePlane } = useSWR<ImpactEffortResponse>(
    overview && adapter.getImpactEffort ? `code-health-plane:${cacheKey}:${planeKey}` : null,
    () => adapter.getImpactEffort!(planeQuery),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  // A new question clears what was picked under the old one.
  useEffect(() => {
    setHighlightedPath(null);
  }, [planeKey]);
  const queueKey = JSON.stringify(query);
  useEffect(() => {
    setSelection(new Map());
  }, [queueKey]);

  const handleStatus = async (findingId: string, next: FindingStatus) => {
    await adapter.updateFindingStatus(findingId, next);
    mutateQueue();
    void mutatePlane();
  };

  // A picked file off this page is fetched on its own and shown above the
  // list, so a click on any point lands on a row.
  const onPage = (queue?.targets ?? []).some((t) => t.file_path === highlightedPath);
  const { data: pinnedQueue } = useSWR<HealthWorkQueueResponse>(
    highlightedPath && queue && !onPage
      ? `code-health-pin:${cacheKey}:${queueKey}:${highlightedPath}`
      : null,
    () => loadHealthWorkQueue({ ...query, offset: 0, limit: 50, search: highlightedPath! }),
    { revalidateOnFocus: false },
  );
  const pinned =
    highlightedPath && !onPage
      ? pinnedQueue?.targets.find((t) => t.file_path === highlightedPath)
      : undefined;

  const onPickPoint = useCallback((path: string) => setHighlightedPath(path), []);

  // Scroll once per pick, after the row exists.
  const scrolledFor = useRef<string | null>(null);
  useEffect(() => {
    if (!highlightedPath) {
      scrolledFor.current = null;
      return;
    }
    if (scrolledFor.current === highlightedPath) return;
    const el = document.querySelector(
      `[data-health-work-item="${CSS.escape(highlightedPath)}"]`,
    );
    if (!el) return;
    scrolledFor.current = highlightedPath;
    el.scrollIntoView?.({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
  }, [highlightedPath, pinned, queue, reducedMotion]);

  const toggleSelect = useCallback((t: HealthWorkItem) => {
    const id = t.primary_finding_id;
    if (!id) return;
    setSelection((cur) => {
      const next = new Map(cur);
      if (next.has(t.file_path)) next.delete(t.file_path);
      else next.set(t.file_path, id);
      return next;
    });
  }, []);
  const selectable = useMemo(
    () =>
      (queue?.targets ?? []).flatMap((t) =>
        t.primary_finding_id ? [{ path: t.file_path, findingId: t.primary_finding_id }] : [],
      ),
    [queue],
  );
  const selectedPaths = useMemo(() => new Set(selection.keys()), [selection]);

  // The repo's whole marker vocabulary, not the loaded rows': a filter must
  // not erase the option that would have widened it.
  const biomarkerOptions = useMemo(
    () => [...new Set((overview?.biomarkers ?? []).map((b) => b.biomarker_type))].sort(),
    [overview],
  );

  // Grouped queue — group order follows the user's sort (first occurrence),
  // not group size, so "Leverage" sorted stays leverage-led inside and out.
  const grouped = useMemo(() => {
    const targets = queue?.targets ?? [];
    // A file led by a history marker has nothing in its code to change, so it
    // reads under Watch, after the files an edit can improve, in every
    // grouping. The lead is a history marker only when the file has no
    // code-shape finding at all.
    const fix = targets.filter((t) => !isWatchOnlyBiomarker(t.primary_biomarker));
    const watch = targets.filter((t) => isWatchOnlyBiomarker(t.primary_biomarker));
    const watchGroup = watch.length ? [{ key: HISTORY_LABEL, targets: watch }] : [];
    if (groupBy === "none") {
      return [...(fix.length ? [{ key: "All", targets: fix }] : []), ...watchGroup];
    }
    const groups = new Map<string, typeof targets>();
    for (const t of fix) {
      let key = "—";
      if (groupBy === "biomarker") key = biomarkerLabel(t.primary_biomarker);
      else if (groupBy === "module") key = t.module ?? "(no module)";
      else if (groupBy === "effort") key = EFFORT_LABEL[t.effort_bucket] ?? t.effort_bucket;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(t);
    }
    return [...[...groups.entries()].map(([key, targets]) => ({ key, targets })), ...watchGroup];
  }, [queue, groupBy]);

  const total = queue?.total ?? 0;
  const findingTotal = queue?.finding_total;
  const shownFrom = total === 0 ? 0 : offset + 1;
  const shownTo = Math.min(offset + PAGE_SIZE, total);
  const historyHidden = queue?.history_only_excluded ?? 0;

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-2">
          <h3 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Fix next
          </h3>
          {queue && !queueValidating ? (
            <span className="text-xs text-[var(--color-text-secondary)]">
              {total.toLocaleString()} {total === 1 ? "file" : "files"}
              {findingTotal != null
                ? ` · ${findingTotal.toLocaleString()} ${
                    findingTotal === 1 ? "finding" : "findings"
                  }`
                : ""}
              {history === "exclude" && historyHidden > 0
                ? ` · ${historyHidden.toLocaleString()} ${
                    historyHidden === 1 ? "file" : "files"
                  } with only history signals hidden`
                : ""}
            </span>
          ) : null}
          {history === "include" || historyHidden > 0 ? (
            <button
              type="button"
              aria-pressed={history === "include"}
              onClick={() => {
                setHistory((h) => (h === "include" ? "exclude" : "include"));
                setOffset(0);
              }}
              className="text-xs text-[var(--color-text-secondary)] underline decoration-dotted underline-offset-2 hover:text-[var(--color-text-primary)]"
            >
              {history === "include" ? "Hide history signals" : "Show history signals"}
            </button>
          ) : null}
          {filtered ? (
            <button
              type="button"
              onClick={clearFilters}
              className="ml-auto text-xs text-[var(--color-text-tertiary)] underline decoration-dotted underline-offset-2 hover:text-[var(--color-text-primary)]"
            >
              Clear filters
            </button>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Filter path…"
              aria-label="Filter by file path"
              className="text-xs pl-7 pr-2 py-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] w-56 focus:outline-none focus:border-[var(--color-border-hover)]"
            />
          </div>
          <FilterSelect
            label="Dimension"
            value={dimension}
            onChange={onFilterChange((v: string) =>
              setDimension(v as HealthDimension | "all"),
            )}
            options={[
              { value: "all", label: "All dimensions" },
              // One name per dimension, shared with every other health surface.
              ...(["defect", "maintainability", "performance", "advisory"] as const).map(
                (d) => ({ value: d, label: DIMENSION_LABEL[d] }),
              ),
            ]}
          />
          <FilterSelect
            label="Marker"
            value={biomarker}
            onChange={onFilterChange(setBiomarker)}
            options={[
              { value: "all", label: "All markers" },
              ...biomarkerOptions.map((b) => ({ value: b, label: biomarkerLabel(b) })),
            ]}
          />
          <FilterSelect
            label="Max effort"
            value={maxEffort}
            onChange={onFilterChange(setMaxEffort)}
            options={[
              { value: "all", label: "Any effort" },
              { value: "S", label: "Small only" },
              { value: "M", label: "Medium and under" },
              { value: "L", label: "Large and under" },
              { value: "XL", label: "Extra large and under" },
            ]}
          />
          <FilterSelect
            label="Sort"
            value={sort ?? "impact_per_effort"}
            onChange={onFilterChange((v: string) =>
              setSort(v as HealthWorkQueueQuery["sort"]),
            )}
            options={[
              { value: "impact_per_effort", label: "Leverage (impact ÷ effort)" },
              { value: "total_impact", label: "Total impact" },
              { value: "score", label: "Worst score" },
              { value: "finding_count", label: "Finding count" },
            ]}
          />
          <FilterSelect
            label="Group"
            value={groupBy}
            onChange={(v) => setGroupBy(v as GroupBy)}
            options={[
              { value: "none", label: "Flat list" },
              { value: "biomarker", label: "By marker" },
              { value: "module", label: "By module" },
              { value: "effort", label: "By effort" },
            ]}
          />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div
            className="flex flex-wrap items-center gap-2"
            role="group"
            aria-label="Severity"
          >
            <span className="text-xs uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Severity
            </span>
            {SEVERITIES.map((s) => (
              <FilterChip
                key={s}
                active={severities.includes(s)}
                onClick={() => toggleSeverity(s)}
              >
                {SEVERITY_LABEL[s]}
              </FilterChip>
            ))}
          </div>
          <div
            className="flex flex-wrap items-center gap-2"
            role="group"
            aria-label="File"
          >
            <span className="text-xs uppercase tracking-wider text-[var(--color-text-tertiary)]">
              File
            </span>
            <FilterChip
              active={onlyHotspots}
              onClick={() => {
                setOnlyHotspots((v) => !v);
                setOffset(0);
              }}
            >
              Hotspots
            </FilterChip>
            <FilterChip
              active={onlyUntested}
              onClick={() => {
                setOnlyUntested((v) => !v);
                setOffset(0);
              }}
            >
              Untested
            </FilterChip>
            <FilterChip
              active={onlyFailing}
              onClick={() => {
                setOnlyFailing((v) => !v);
                setOffset(0);
              }}
            >
              Below green
            </FilterChip>
          </div>
        </div>

        {queueLoading && !queue ? (
          <div className="grid gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-28 w-full" />
            ))}
          </div>
        ) : queueError && !queue ? (
          <ApiError
            size="compact"
            title="Couldn't load findings"
            message={toFriendlyMessage(queueError)}
            onRetry={() => void mutateQueue()}
          />
        ) : total === 0 ? (
          filtered ? (
            <EmptyState
              tone="filtered"
              title="Nothing matches these filters"
              description="This view lists files carrying findings, ranked by leverage. Widen a filter to see more."
              action={{ label: "Clear filters", onClick: clearFilters }}
            />
          ) : (
            <EmptyState
              tone="positive"
              title="No open findings"
              description={`Health was scored across ${(overview?.summary.file_count ?? 0).toLocaleString()} files and none carries an open finding. New work appears here after the next index update.`}
            />
          )
        ) : (
          <div
            className={
              queueValidating
                ? "space-y-6 opacity-60 transition-opacity"
                : "space-y-6 transition-opacity"
            }
            aria-busy={queueValidating}
          >
            {/* The whole filtered set, from one aggregate call. A click finds
                the file in the list below; the row opens the drawer. */}
            {plane && plane.total > 0 ? (
              <ImpactEffortQuadrant
                data={plane}
                selectedPath={highlightedPath}
                onSelect={onPickPoint}
              />
            ) : null}
            {pinned ? (
              <section className="space-y-2" aria-label="Selected in the graph">
                <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                  Selected in the graph{" "}
                  <span className="normal-case tracking-normal text-[var(--color-text-secondary)]">
                    (not on this page)
                  </span>
                </h3>
                <HealthWorkQueueList
                  targets={[pinned]}
                  onSelect={(t) => setSelectedFile(t.file_path)}
                  onStatusChange={handleStatus}
                  onGeneratePrompt={(t) => void openPrompt(t)}
                  onLoadFindings={loadFindings}
                  onLoadOpportunity={loadOpportunity}
                  refactoringOpportunityHref={adapter.refactoringOpportunityHref}
                  highlightedPath={highlightedPath}
                />
              </section>
            ) : null}
            <div className="space-y-6">
              {grouped.map((g) => (
                <section key={g.key} className="space-y-2">
                  {groupBy !== "none" || g.key === HISTORY_LABEL ? (
                    <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                      {g.key}{" "}
                      <span className="text-[var(--color-text-secondary)]">
                        ({g.targets.length}
                        {groupBy === "none" ? " on this page" : ""})
                      </span>
                    </h3>
                  ) : null}
                  {g.key === HISTORY_LABEL ? (
                    <p className="max-w-[72ch] text-xs text-[var(--color-text-tertiary)]">
                      {HISTORY_EXPLAINER}
                    </p>
                  ) : null}
                  <HealthWorkQueueList
                    targets={g.targets}
                    onSelect={(t) => setSelectedFile(t.file_path)}
                    onStatusChange={handleStatus}
                    onGeneratePrompt={(t) => void openPrompt(t)}
                    onLoadFindings={loadFindings}
                    onLoadOpportunity={loadOpportunity}
                    refactoringOpportunityHref={adapter.refactoringOpportunityHref}
                    highlightedPath={highlightedPath}
                    selectedPaths={selectedPaths}
                    onToggleSelect={toggleSelect}
                    onClearFilters={filtered ? clearFilters : undefined}
/>
                </section>
              ))}
            </div>

            <div className="flex items-center justify-between gap-2 text-xs text-[var(--color-text-tertiary)]">
              <span>
                Showing {shownFrom.toLocaleString()}–{shownTo.toLocaleString()} of{" "}
                {total.toLocaleString()} files
              </span>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  Prev
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={offset + PAGE_SIZE >= total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            </div>
            <BulkTriageBar
              selection={selection}
              selectablePaths={selectable}
              onSelectAll={() =>
                setSelection((cur) => {
                  const next = new Map(cur);
                  for (const r of selectable) next.set(r.path, r.findingId);
                  return next;
                })
              }
              onClear={() => setSelection(new Map())}
              updateStatus={(id, status) => adapter.updateFindingStatus(id, status)}
              onDone={(failed) => {
                setSelection((cur) => {
                  const keep = new Set(failed);
                  return new Map([...cur].filter(([path]) => keep.has(path)));
                });
                mutateQueue();
                void mutatePlane();
              }}
            />
          </div>
        )}
      </div>

      {hotFunctionFindings && hotFunctionFindings.length >= 3 ? (
        <HotFunctionsPanel
          findings={hotFunctionFindings}
          collapsible
          onSelect={(f) => setSelectedFile(f.file_path)}
          symbolHrefFor={(f) => (f.symbol_id ? adapter.symbolHref?.(f.symbol_id) : undefined)}
        />
      ) : null}

      {couplingFindings && couplingFindings.length >= 3 ? (
        <HiddenCouplingList
          findings={couplingFindings}
          collapsible
          onSelect={(path) => setSelectedFile(path)}
        />
      ) : null}

      {adapter.renderFileDrawer({
        filePath: selectedFile,
        onClose: () => setSelectedFile(null),
      })}

      <AiPromptModal
        open={promptTarget !== null}
        onOpenChange={(open) => {
          if (!open) setPromptTarget(null);
        }}
        filePath={promptTarget?.file_path ?? null}
        chatContext={fileChatContext(promptTarget?.file_path)}
        title="AI fix prompt"
        description="A ready-to-paste prompt that gives your AI coding agent every marker, line range, score deduction, and constraint needed to refactor this file in one focused pass."
        getPrompt={
          promptTarget ? (flavor) => buildAiPrompt({ target: promptTarget, flavor }) : null
        }
      />
    </div>
  );
}
