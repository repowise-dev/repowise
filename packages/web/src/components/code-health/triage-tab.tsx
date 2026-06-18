"use client";

/**
 * Triage tab — the "what do I fix next" queue. KPI strip, the ranked
 * fix-next list (refactoring-target ranking + file inventory merged into one
 * view), a findings sidebar coordinated by the same severity filter, and the
 * function-level panels.
 */

import { useMemo, useState } from "react";
import useSWR from "swr";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { Button } from "@repowise-dev/ui/ui/button";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  AiPromptModal,
  HealthKpiCards,
  HealthFileTable,
  BiomarkerList,
  HotFunctionsPanel,
  HiddenCouplingList,
  CodeHealthMap,
  RecalibrationBanner,
  RefactoringTargetList,
  DefectAccuracyCard,
  FileSpotlight,
  FilterSelect,
  FilterChip,
  ViewToggle,
  ImpactEffortQuadrant,
  biomarkerLabel,
  buildAiPrompt,
  type CodeHealthMapFile,
  type CodeHealthOverlay,
  type FileSortField,
  type FindingStatus,
  type RefactoringTarget,
  type Severity,
} from "@repowise-dev/ui/health";
import { CollapsibleSection } from "@repowise-dev/ui/shared/collapsible-section";
import { Search } from "lucide-react";
import {
  getHealthOverview,
  getRefactoringTargets,
  listHealthFiles,
  listHealthFindings,
  updateFindingStatus,
  type HealthFinding,
  type HealthOverviewResponse,
  type HealthTrendResponse,
  type HealthFilesResponse,
  type RefactoringTargetsResponse,
  type RefactoringQuery,
} from "@/lib/api/code-health";
import { HealthFileDrawerHost } from "@/components/health/health-file-drawer-host";

const PAGE_SIZE = 50;
const QUEUE_PAGE = 200;
const QUEUE_MAX = 500;

type GroupBy = "none" | "biomarker" | "module" | "effort";
type QueueView = "queue" | "files";

const EFFORT_LABEL: Record<string, string> = {
  S: "Small (≤40 NLOC)",
  M: "Medium (≤150 NLOC)",
  L: "Large (≤400 NLOC)",
  XL: "Extra large (>400 NLOC)",
};

export function TriageTab({
  repoId: id,
  trend,
  overlay = "health",
  onOverlayChange,
  mapFiles,
  overlayLoading,
}: {
  repoId: string;
  /** Trend fetched once at the page level — feeds the KPI sparklines. */
  trend?: HealthTrendResponse;
  /** Active map lens, owned by the page so the spine is shared across tabs. */
  overlay?: CodeHealthOverlay;
  onOverlayChange?: (overlay: CodeHealthOverlay) => void;
  /** Map files fetched once at the page level (shared across overlays). */
  mapFiles?: HealthFilesResponse;
  /** The active lens's per-file signal is still loading (e.g. churn). */
  overlayLoading?: boolean;
}) {
  const { data: overview, isLoading, error, mutate } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${id}`,
    () => getHealthOverview(id, 25),
    { revalidateOnFocus: false },
  );
  const { data: hotFunctionFindings } = useSWR<HealthFinding[]>(
    `code-health-hot-functions:${id}`,
    async () => {
      const types = [
        "function_hotspot",
        "code_age_volatility",
        "complex_conditional",
      ] as const;
      const batches = await Promise.all(
        types.map((t) =>
          listHealthFindings(id, { biomarker_type: t, limit: 100 }).catch(
            () => [] as HealthFinding[],
          ),
        ),
      );
      return batches.flat();
    },
    { revalidateOnFocus: false },
  );

  const { data: couplingFindings } = useSWR<HealthFinding[]>(
    `code-health-hidden-coupling:${id}`,
    () =>
      listHealthFindings(id, {
        biomarker_type: "hidden_coupling",
        limit: 100,
      }).catch(() => []),
    { revalidateOnFocus: false },
  );

  // ---- One filter set coordinates the queue AND the findings sidebar ----
  const [minSeverity, setMinSeverity] = useState<Severity | "all">("all");
  const [biomarker, setBiomarker] = useState<string>("all");
  const [maxEffort, setMaxEffort] = useState<string>("all");
  const [sort, setSort] = useState<RefactoringQuery["sort"]>("impact_per_effort");
  const [groupBy, setGroupBy] = useState<GroupBy>("none");
  const [queueLimit, setQueueLimit] = useState(QUEUE_PAGE);
  const [view, setView] = useState<QueueView>("queue");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [promptTarget, setPromptTarget] = useState<RefactoringTarget | null>(null);

  // ---- Map-driven UI: rail spotlight + filename dim + collapsible list ----
  const [hoverFile, setHoverFile] = useState<CodeHealthMapFile | null>(null);
  const [mapQuery, setMapQuery] = useState("");

  const queueKey = useMemo(
    () => JSON.stringify({ id, biomarker, minSeverity, maxEffort, sort, queueLimit }),
    [id, biomarker, minSeverity, maxEffort, sort, queueLimit],
  );
  const {
    data: queue,
    isLoading: queueLoading,
    mutate: mutateQueue,
  } = useSWR<RefactoringTargetsResponse>(
    overview ? `code-health-queue:${queueKey}` : null,
    () =>
      getRefactoringTargets(id, {
        limit: queueLimit,
        biomarker: biomarker === "all" ? undefined : biomarker,
        min_severity: minSeverity === "all" ? undefined : minSeverity,
        max_effort: maxEffort === "all" ? undefined : maxEffort,
        sort,
      }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  // ---- All-files inventory view ----
  const [sortField, setSortField] = useState<FileSortField>("score");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");
  const [search, setSearch] = useState("");
  const [onlyHotspots, setOnlyHotspots] = useState(false);
  const [onlyUntested, setOnlyUntested] = useState(false);
  const [onlyFailing, setOnlyFailing] = useState(false);
  const [offset, setOffset] = useState(0);

  const filesKey = useMemo(
    () =>
      JSON.stringify({
        id,
        sortField,
        sortOrder,
        search,
        onlyHotspots,
        onlyUntested,
        onlyFailing,
        offset,
      }),
    [id, sortField, sortOrder, search, onlyHotspots, onlyUntested, onlyFailing, offset],
  );

  const { data: files, isLoading: filesLoading } = useSWR<HealthFilesResponse>(
    overview && view === "files" ? `code-health-files:${filesKey}` : null,
    () =>
      listHealthFiles(id, {
        sort: sortField,
        order: sortOrder,
        search: search || undefined,
        only_hotspots: onlyHotspots || undefined,
        only_untested: onlyUntested || undefined,
        only_failing: onlyFailing || undefined,
        offset,
        limit: PAGE_SIZE,
      }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  const handleSort = (field: FileSortField) => {
    if (field === sortField) {
      setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      // Score: asc (worst first). Counts: desc. Path: asc.
      setSortOrder(["score", "line_coverage_pct", "file_path"].includes(field) ? "asc" : "desc");
    }
    setOffset(0);
  };

  const handleStatus = async (findingId: string, status: FindingStatus) => {
    await updateFindingStatus(id, findingId, status);
    mutateQueue();
  };

  const biomarkerOptions = useMemo(() => {
    const set = new Set<string>();
    (overview?.biomarkers ?? []).forEach((b) => set.add(b.biomarker_type));
    (queue?.targets ?? []).forEach((t) => t.biomarkers.forEach((b) => set.add(b)));
    return [...set].sort();
  }, [overview, queue]);

  // Grouped queue — group order follows the user's sort (first occurrence),
  // not group size, so "Leverage" sorted stays leverage-led inside and out.
  const grouped = useMemo(() => {
    const targets = queue?.targets ?? [];
    if (groupBy === "none") return [{ key: "All", targets }];
    const groups = new Map<string, typeof targets>();
    for (const t of targets) {
      let key = "—";
      if (groupBy === "biomarker") key = biomarkerLabel(t.primary_biomarker);
      else if (groupBy === "module") key = t.module ?? "(no module)";
      else if (groupBy === "effort") key = EFFORT_LABEL[t.effort_bucket] ?? t.effort_bucket;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(t);
    }
    return [...groups.entries()].map(([key, targets]) => ({ key, targets }));
  }, [queue, groupBy]);

  const avgSeries = trend?.history?.slice().reverse().map((p) => p.average_health);
  const hotspotSeries = trend?.history?.slice().reverse().map((p) => p.hotspot_health);
  const worstSeries = trend?.history
    ?.slice()
    .reverse()
    .map((p) => p.worst_performer_score ?? 0);

  return (
    <div className="space-y-6">
      {isLoading ? (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)] flex items-center justify-between gap-2">
          <span>Couldn&apos;t load health data. Run <code>repowise init</code> to populate.</span>
          <Button size="sm" variant="outline" onClick={() => mutate()}>Retry</Button>
        </div>
      ) : overview ? (
        <>
          <RecalibrationBanner repoId={id} />
          <HealthKpiCards
            summary={overview.summary}
            distribution={overview.distribution}
            averageHistory={avgSeries}
            hotspotHistory={hotspotSeries}
            worstHistory={worstSeries}
            averageDelta={trend?.summary?.average_delta ?? undefined}
            hotspotDelta={trend?.summary?.hotspot_delta ?? undefined}
          />

          {overview.defect_accuracy ? (
            <DefectAccuracyCard data={overview.defect_accuracy} collapsible />
          ) : null}

          {/* Hero: the Code Health Map — modules as nested bubbles, files
              sized by lines of code and colored by health. The centerpiece. */}
          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px] gap-6">
            <div className="space-y-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
                  Code health map
                </h2>
                <span className="text-xs text-[var(--color-text-tertiary)]">
                  {mapFiles ? `${mapFiles.total.toLocaleString()} files` : "loading…"} · click a module to
                  zoom, a file to open
                </span>
              </div>
              {!mapFiles ? (
                <Skeleton className="w-full rounded-xl" style={{ height: 664 }} />
              ) : (
                <CodeHealthMap
                  files={mapFiles.files}
                  search={mapQuery}
                  selectedPath={selectedFile}
                  onSelectFile={(p) => setSelectedFile(p)}
                  onHoverFile={setHoverFile}
                  minHeight={664}
                  overlay={overlay}
                  onOverlayChange={onOverlayChange}
                  overlayLoading={overlayLoading}
                />
              )}
            </div>

            {/* Inspector rail — height-matched to the map (master–detail).
                Search + the file you're inspecting sit on top; the findings
                list scrolls in place so the rail never outgrows the map. */}
            <aside className="flex flex-col gap-3 lg:sticky lg:top-4 lg:h-[700px]">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
                <input
                  value={mapQuery}
                  onChange={(e) => setMapQuery(e.target.value)}
                  placeholder="Find a file in the map…"
                  className="w-full text-xs pl-7 pr-2 py-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] focus:outline-none focus:border-[var(--color-border-strong)]"
                />
              </div>

              <FileSpotlight file={hoverFile} onOpen={(p) => setSelectedFile(p)} />

              <div className="flex items-center gap-2">
                <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] mr-auto">
                  Findings
                </h2>
                <select
                  value={minSeverity}
                  onChange={(e) => setMinSeverity(e.target.value as Severity | "all")}
                  aria-label="Minimum severity (filters the queue too)"
                  className="text-xs px-2 py-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]"
                >
                  <option value="all">All severities</option>
                  <option value="low">Low+</option>
                  <option value="medium">Medium+</option>
                  <option value="high">High+</option>
                  <option value="critical">Critical</option>
                </select>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto pr-1 lg:pb-0 pb-1">
                <BiomarkerList
                  findings={overview.top_findings}
                  grouped
                  minSeverity={minSeverity === "all" ? undefined : minSeverity}
                  onSelect={(f) => setSelectedFile(f.file_path)}
                />
              </div>
            </aside>
          </div>

          {/* Collapsible: the full ranked queue + file inventory. The severity
              filter is shared with the rail above (one control, no duplicate). */}
          <CollapsibleSection
            title="Targets &amp; findings list"
            hint={queue?.total != null ? `${queue.total} candidates` : ""}
            defaultOpen={false}
          >
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] mr-auto">
                  Fix next
                </h3>
                <ViewToggle
                  value={view}
                  onChange={setView}
                  options={[
                    { value: "queue", label: "Queue" },
                    { value: "files", label: "All files" },
                  ]}
                />
              </div>

              {view === "queue" ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <FilterSelect
                      label="Biomarker"
                      value={biomarker}
                      onChange={setBiomarker}
                      options={[
                        { value: "all", label: "All biomarkers" },
                        ...biomarkerOptions.map((b) => ({ value: b, label: biomarkerLabel(b) })),
                      ]}
                    />
                    <FilterSelect
                      label="Max effort"
                      value={maxEffort}
                      onChange={setMaxEffort}
                      options={[
                        { value: "all", label: "Any effort" },
                        { value: "S", label: "Small only" },
                        { value: "M", label: "Medium+" },
                        { value: "L", label: "Large+" },
                      ]}
                    />
                    <FilterSelect
                      label="Sort"
                      value={sort ?? "impact_per_effort"}
                      onChange={(v) => setSort(v as RefactoringQuery["sort"])}
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
                        { value: "biomarker", label: "By biomarker" },
                        { value: "module", label: "By module" },
                        { value: "effort", label: "By effort" },
                      ]}
                    />
                  </div>

                  {queueLoading && !queue ? (
                    <div className="grid gap-3">
                      {Array.from({ length: 4 }).map((_, i) => (
                        <Skeleton key={i} className="h-28 w-full" />
                      ))}
                    </div>
                  ) : !queue || queue.targets.length === 0 ? (
                    <EmptyState
                      title="No findings match the current filters"
                      description="Try widening the severity or effort filters, or run repowise health to populate findings."
                    />
                  ) : (
                    <>
                      {/* Leverage at a glance — impact vs effort, click a dot
                          to open the file. Replaces the text-dense ranking as
                          the first read of the queue. */}
                      <ImpactEffortQuadrant
                        points={(queue?.targets ?? []).map((t) => ({
                          file_path: t.file_path,
                          total_impact: t.total_impact,
                          effort_bucket: t.effort_bucket,
                          nloc: t.nloc,
                          score: t.score,
                        }))}
                        onSelect={(p) => setSelectedFile(p.file_path)}
                      />
                      <div className="space-y-6">
                        {grouped.map((g) => (
                          <section key={g.key} className="space-y-2">
                            {groupBy !== "none" ? (
                              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                                {g.key}{" "}
                                <span className="text-[var(--color-text-secondary)]">
                                  ({g.targets.length})
                                </span>
                              </h3>
                            ) : null}
                            <RefactoringTargetList
                              targets={g.targets}
                              onSelect={(t) => setSelectedFile(t.file_path)}
                              onStatusChange={handleStatus}
                              onGeneratePrompt={(t) => setPromptTarget(t)}
                            />
                          </section>
                        ))}
                      </div>

                      <div className="flex items-center justify-between gap-2 text-xs text-[var(--color-text-tertiary)]">
                        <span>
                          Showing {queue.targets.length} of {queue.total} candidates
                        </span>
                        {queue.total > queue.targets.length && queueLimit < QUEUE_MAX ? (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => setQueueLimit(QUEUE_MAX)}
                          >
                            Load more
                          </Button>
                        ) : null}
                      </div>
                    </>
                  )}
                </>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="relative">
                      <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
                      <input
                        value={search}
                        onChange={(e) => {
                          setSearch(e.target.value);
                          setOffset(0);
                        }}
                        placeholder="Filter path…"
                        className="text-xs pl-7 pr-2 py-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] w-56 focus:outline-none focus:border-[var(--color-border-strong)]"
                      />
                    </div>
                    <FilterChip active={onlyHotspots} onClick={() => { setOnlyHotspots((v) => !v); setOffset(0); }}>
                      Hotspots
                    </FilterChip>
                    <FilterChip active={onlyUntested} onClick={() => { setOnlyUntested((v) => !v); setOffset(0); }}>
                      Untested
                    </FilterChip>
                    <FilterChip active={onlyFailing} onClick={() => { setOnlyFailing((v) => !v); setOffset(0); }}>
                      Failing
                    </FilterChip>
                    <span className="text-xs text-[var(--color-text-tertiary)] ml-auto">
                      {files?.total != null ? `${files.total.toLocaleString()} files` : ""}
                    </span>
                  </div>

                  {filesLoading && !files ? (
                    <Skeleton className="h-64 w-full rounded-lg" />
                  ) : (
                    <HealthFileTable
                      files={files?.files ?? []}
                      sortField={sortField}
                      sortOrder={sortOrder}
                      onSort={handleSort}
                      onSelect={(f) => setSelectedFile(f.file_path)}
                      selectedPath={selectedFile}
                    />
                  )}

                  {files && files.total > PAGE_SIZE ? (
                    <div className="flex items-center justify-between gap-2 text-xs text-[var(--color-text-tertiary)]">
                      <span>
                        Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, files.total)} of {files.total}
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
                          disabled={offset + PAGE_SIZE >= files.total}
                          onClick={() => setOffset(offset + PAGE_SIZE)}
                        >
                          Next
                        </Button>
                      </div>
                    </div>
                  ) : null}
                </>
              )}
            </div>
          </CollapsibleSection>

          {hotFunctionFindings && hotFunctionFindings.length >= 3 ? (
            <HotFunctionsPanel
              findings={hotFunctionFindings}
              collapsible
              onSelect={(f) => setSelectedFile(f.file_path)}
              symbolHrefFor={(f) =>
                f.symbol_id ? symbolEntityPath(`/repos/${id}`, f.symbol_id) : undefined
              }
            />
          ) : null}

          {couplingFindings && couplingFindings.length >= 3 ? (
            <HiddenCouplingList
              findings={couplingFindings}
              collapsible
              onSelect={(path) => setSelectedFile(path)}
            />
          ) : null}
        </>
      ) : null}

      <HealthFileDrawerHost
        repoId={id}
        filePath={selectedFile}
        onClose={() => setSelectedFile(null)}
      />

      <AiPromptModal
        open={promptTarget !== null}
        onOpenChange={(open) => {
          if (!open) setPromptTarget(null);
        }}
        filePath={promptTarget?.file_path}
        title="AI fix prompt"
        description="A ready-to-paste prompt that gives your AI coding agent every biomarker, line range, score deduction, and constraint needed to refactor this file in one focused pass."
        getPrompt={
          promptTarget
            ? (flavor) => buildAiPrompt({ target: promptTarget, flavor })
            : null
        }
      />
    </div>
  );
}
