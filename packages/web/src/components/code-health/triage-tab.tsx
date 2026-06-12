"use client";

/**
 * Triage tab — the former /health overview page body, hosted inside the
 * consolidated Code Health section. Phase 3 of the UX overhaul evolves this
 * into the ranked "fix next" queue.
 */

import { useMemo, useState } from "react";
import useSWR from "swr";
import { HeartPulse, RotateCw, Search } from "lucide-react";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { Button } from "@repowise-dev/ui/ui/button";
import {
  HealthKpiCards,
  HealthFileTable,
  BiomarkerList,
  ModuleRollupList,
  HotFunctionsPanel,
  HiddenCouplingList,
  RecalibrationBanner,
  DefectAccuracyCard,
  type FileSortField,
  type Severity,
} from "@repowise-dev/ui/health";
import {
  getHealthOverview,
  getHealthTrend,
  listHealthFiles,
  listHealthFindings,
  type HealthFinding,
  type HealthOverviewResponse,
  type HealthTrendResponse,
  type HealthFilesResponse,
} from "@/lib/api/code-health";
import { HealthPageChrome } from "@/components/health/health-page-chrome";
import { HealthFileDrawerHost } from "@/components/health/health-file-drawer-host";

const PAGE_SIZE = 50;

export function TriageTab({ repoId: id }: { repoId: string }) {
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

  const { data: trend } = useSWR<HealthTrendResponse>(
    `code-health-trend:${id}`,
    () => getHealthTrend(id, 20),
    { revalidateOnFocus: false },
  );

  // File table state
  const [sortField, setSortField] = useState<FileSortField>("score");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");
  const [search, setSearch] = useState("");
  const [onlyHotspots, setOnlyHotspots] = useState(false);
  const [onlyUntested, setOnlyUntested] = useState(false);
  const [onlyFailing, setOnlyFailing] = useState(false);
  const [offset, setOffset] = useState(0);
  const [minSeverity, setMinSeverity] = useState<Severity | "all">("all");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

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
    overview ? `code-health-files:${filesKey}` : null,
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

  const avgSeries = trend?.history?.slice().reverse().map((p) => p.average_health);
  const hotspotSeries = trend?.history?.slice().reverse().map((p) => p.hotspot_health);
  const worstSeries = trend?.history
    ?.slice()
    .reverse()
    .map((p) => p.worst_performer_score ?? 0);

  return (
    <div className="space-y-6">
      <HealthPageChrome
        repoId={id}
        active="overview"
        basePath={`/repos/${id}/code-health`}
        title="Code Health"
        icon={<HeartPulse className="h-5 w-5 text-[var(--color-success)]" />}
        subtitle="Per-file health scores from CCN, nesting, duplication, coverage, and ownership biomarkers."
        meta={overview?.meta}
        actions={
          <Button size="sm" variant="outline" onClick={() => mutate()}>
            <RotateCw className="h-3.5 w-3.5 mr-1.5" /> Refresh
          </Button>
        }
      />

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
            averageHistory={avgSeries}
            hotspotHistory={hotspotSeries}
            worstHistory={worstSeries}
            averageDelta={trend?.summary?.average_delta ?? undefined}
            hotspotDelta={trend?.summary?.hotspot_delta ?? undefined}
          />

          {overview.defect_accuracy ? (
            <DefectAccuracyCard data={overview.defect_accuracy} />
          ) : null}

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] mr-auto">
                  Files {files?.total != null ? `(${files.total.toLocaleString()})` : ""}
                </h2>
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
            </div>

            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] mr-auto">
                  Findings
                </h2>
                <select
                  value={minSeverity}
                  onChange={(e) => setMinSeverity(e.target.value as Severity | "all")}
                  className="text-xs px-2 py-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]"
                >
                  <option value="all">All severities</option>
                  <option value="low">Low+</option>
                  <option value="medium">Medium+</option>
                  <option value="high">High+</option>
                  <option value="critical">Critical</option>
                </select>
              </div>
              <BiomarkerList
                findings={overview.top_findings}
                grouped
                minSeverity={minSeverity === "all" ? undefined : minSeverity}
                onSelect={(f) => setSelectedFile(f.file_path)}
              />
            </div>
          </div>

          {hotFunctionFindings && hotFunctionFindings.length >= 3 ? (
            <HotFunctionsPanel
              findings={hotFunctionFindings}
              onSelect={(f) => setSelectedFile(f.file_path)}
            />
          ) : null}

          {couplingFindings && couplingFindings.length >= 3 ? (
            <HiddenCouplingList
              findings={couplingFindings}
              onSelect={(path) => setSelectedFile(path)}
            />
          ) : null}

          {overview.modules && overview.modules.length > 0 ? (
            <div className="space-y-2">
              <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                By module
              </h2>
              <ModuleRollupList modules={overview.modules} />
            </div>
          ) : null}
        </>
      ) : null}

      <HealthFileDrawerHost
        repoId={id}
        filePath={selectedFile}
        onClose={() => setSelectedFile(null)}
      />
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-xs rounded-md px-2 py-1 border transition-colors ${
        active
          ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)] border-[var(--color-accent-primary)]/50"
          : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-strong)]"
      }`}
    >
      {children}
    </button>
  );
}
