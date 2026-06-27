"use client";

/**
 * Findings view — the engineer's workbench. The ranked fix-next queue
 * (refactoring targets + file inventory), the cross-function performance-risk
 * panel, and the function-level / hidden-coupling panels.
 *
 * Split out of {@link TriageView} so the landing surface stays an airy
 * proof + map overview and the dense drill-down lives behind its own tab.
 * Presentation + orchestration only: the host injects data, links, and the
 * file-detail drawer through a {@link CodeHealthAdapter}.
 */

import { useMemo, useState } from "react";
import useSWR from "swr";
import { Gauge, Search } from "lucide-react";
import type {
  HealthFinding,
  HealthFilesResponse,
  HealthOverviewResponse,
  RefactoringQuery,
  RefactoringTargetsResponse,
} from "@repowise-dev/types/health";

import { Skeleton } from "../ui/skeleton";
import { Button } from "../ui/button";
import { EmptyState } from "../shared/empty-state";
import { CollapsibleSection } from "../shared/collapsible-section";

import { AiPromptModal } from "./ai-prompt-modal";
import { HealthFileTable, type FileSortField } from "./file-table";
import { BiomarkerList } from "./biomarker-list";
import { HotFunctionsPanel } from "./hot-functions-panel";
import { HiddenCouplingList } from "./hidden-coupling-list";
import {
  RefactoringTargetList,
  type FindingStatus,
  type RefactoringTarget,
} from "./refactoring-target-list";
import { FilterSelect, FilterChip, ViewToggle } from "./code-health-controls";
import { ImpactEffortQuadrant } from "./impact-effort-quadrant";
import { biomarkerLabel } from "./biomarker-glossary";
import { buildAiPrompt } from "./ai-prompt-builder";
import { type Severity } from "./tokens";
import type { CodeHealthAdapter } from "./code-health-adapter";

const PAGE_SIZE = 50;
const QUEUE_PAGE = 200;
const QUEUE_MAX = 500;

type GroupBy = "none" | "biomarker" | "module" | "effort";
type QueueView = "queue" | "files";

/** Function-level biomarker types fed to the Hot functions panel. */
const HOT_FN_TYPES = ["function_hotspot", "code_age_volatility", "complex_conditional"];

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
  // queue fetch and seed the biomarker filter options.
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

  // Every open performance-pillar finding (the cross-function N+1 / I/O-in-loop
  // moat), for the dedicated Performance risks panel.
  const { data: perfFindings } = useSWR<HealthFinding[]>(
    `code-health-perf-findings:${cacheKey}`,
    () =>
      adapter
        .listFindings({ dimension: "performance", limit: 200 })
        .catch(() => [] as HealthFinding[]),
    { revalidateOnFocus: false },
  );

  // ---- Queue filters ----
  const [minSeverity, setMinSeverity] = useState<Severity | "all">("all");
  const [biomarker, setBiomarker] = useState<string>("all");
  const [maxEffort, setMaxEffort] = useState<string>("all");
  const [sort, setSort] = useState<RefactoringQuery["sort"]>("impact_per_effort");
  const [groupBy, setGroupBy] = useState<GroupBy>("none");
  const [queueLimit, setQueueLimit] = useState(QUEUE_PAGE);
  const [view, setView] = useState<QueueView>("queue");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [promptTarget, setPromptTarget] = useState<RefactoringTarget | null>(null);

  const queueKey = useMemo(
    () =>
      JSON.stringify({ cacheKey, biomarker, minSeverity, maxEffort, sort, queueLimit }),
    [cacheKey, biomarker, minSeverity, maxEffort, sort, queueLimit],
  );
  const {
    data: queue,
    isLoading: queueLoading,
    mutate: mutateQueue,
  } = useSWR<RefactoringTargetsResponse>(
    overview ? `code-health-queue:${queueKey}` : null,
    () =>
      adapter.getRefactoringTargets({
        limit: queueLimit,
        ...(biomarker !== "all" && { biomarker }),
        ...(minSeverity !== "all" && { min_severity: minSeverity }),
        ...(maxEffort !== "all" && { max_effort: maxEffort }),
        ...(sort && { sort }),
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
        cacheKey,
        sortField,
        sortOrder,
        search,
        onlyHotspots,
        onlyUntested,
        onlyFailing,
        offset,
      }),
    [cacheKey, sortField, sortOrder, search, onlyHotspots, onlyUntested, onlyFailing, offset],
  );

  const { data: files, isLoading: filesLoading } = useSWR<HealthFilesResponse>(
    overview && view === "files" ? `code-health-files:${filesKey}` : null,
    () =>
      adapter.listFiles({
        sort: sortField,
        order: sortOrder,
        ...(search && { search }),
        ...(onlyHotspots && { only_hotspots: true }),
        ...(onlyUntested && { only_untested: true }),
        ...(onlyFailing && { only_failing: true }),
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
    await adapter.updateFindingStatus(findingId, status);
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

  return (
    <div className="space-y-6">
      {/* Performance risks — the cross-function N+1 / I/O-in-loop moat, given a
          dedicated home. Only rendered when risks actually exist (high
          precision, low recall), so a clean repo stays uncluttered. */}
      {perfFindings && perfFindings.length > 0 ? (
        <CollapsibleSection
          title={
            <span className="inline-flex items-center gap-2">
              <Gauge className="h-4 w-4 text-[var(--color-info)]" />
              Performance risks
            </span>
          }
          hint={`${perfFindings.length} ${perfFindings.length === 1 ? "risk" : "risks"}`}
          defaultOpen
        >
          <div className="space-y-3">
            <p className="text-xs text-[var(--color-text-secondary)]">
              Static performance risk: a DB, network, filesystem, or subprocess call
              per loop iteration, detected across function boundaries via the call
              graph. High precision, low recall, never blended into the defect score.
            </p>
            <BiomarkerList
              findings={perfFindings}
              grouped
              onSelect={(f) => setSelectedFile(f.file_path)}
            />
          </div>
        </CollapsibleSection>
      ) : null}

      {/* The full ranked queue + file inventory. */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] mr-auto">
            Fix next
            {queue?.total != null ? (
              <span className="ml-2 normal-case tracking-normal text-[var(--color-text-secondary)]">
                {queue.total} candidates
              </span>
            ) : null}
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
                label="Marker"
                value={biomarker}
                onChange={setBiomarker}
                options={[
                  { value: "all", label: "All markers" },
                  ...biomarkerOptions.map((b) => ({ value: b, label: biomarkerLabel(b) })),
                ]}
              />
              <FilterSelect
                label="Severity"
                value={minSeverity}
                onChange={(v) => setMinSeverity(v as Severity | "all")}
                options={[
                  { value: "all", label: "All severities" },
                  { value: "low", label: "Low+" },
                  { value: "medium", label: "Medium+" },
                  { value: "high", label: "High+" },
                  { value: "critical", label: "Critical" },
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
                  { value: "biomarker", label: "By marker" },
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
                {/* Leverage at a glance — impact vs effort, click a dot to open
                    the file. Replaces the text-dense ranking as the first read. */}
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

      {hotFunctionFindings && hotFunctionFindings.length >= 3 ? (
        <HotFunctionsPanel
          findings={hotFunctionFindings}
          collapsible
          onSelect={(f) => setSelectedFile(f.file_path)}
          symbolHrefFor={(f) =>
            f.symbol_id ? adapter.symbolHref?.(f.symbol_id) : undefined
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
        title="AI fix prompt"
        description="A ready-to-paste prompt that gives your AI coding agent every marker, line range, score deduction, and constraint needed to refactor this file in one focused pass."
        getPrompt={
          promptTarget
            ? (flavor) => buildAiPrompt({ target: promptTarget, flavor })
            : null
        }
      />
    </div>
  );
}
