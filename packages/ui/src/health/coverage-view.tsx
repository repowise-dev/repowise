"use client";

/**
 * Coverage view — test-coverage summary, risk × coverage scatter, untested
 * hotspots (with the dependents/commit context from the findings), module
 * rollup, and the per-file table. Rows link to the file page's coverage tab
 * (line-level heatmap).
 *
 * Presentation + orchestration only: the host injects data fetching, links,
 * and navigation through a {@link CodeHealthAdapter}.
 */

import { useMemo, useState } from "react";
import { ArrowUpRight, Sparkles } from "lucide-react";
import useSWR from "swr";
import type {
  CoverageFileRow,
  HealthCoverageResponse,
  HealthFinding,
} from "@repowise-dev/types/health";

import { Skeleton } from "../ui/skeleton";
import { EmptyState } from "../shared/empty-state";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { MetricCard } from "../shared/metric-card";
import { CollapsibleSection } from "../shared/collapsible-section";

import { AiPromptModal } from "./ai-prompt-modal";
import { CoverageBar } from "./coverage-bar";
import { ModuleCoverageList } from "./module-coverage-list";
import {
  UntestedHotspotWarning,
  type UntestedHotspotEntry,
} from "./untested-hotspot-warning";
import { RiskCoverageScatter } from "./risk-coverage-scatter";
import {
  buildCoverageAiPrompt,
  type CoverageFilePromptInput,
} from "./ai-prompt-builder";
import { scoreBadgeClass } from "./tokens";
import type { CodeHealthAdapter } from "./code-health-adapter";

export function CoverageView({ adapter }: { adapter: CodeHealthAdapter }) {
  const [promptRow, setPromptRow] = useState<CoverageFilePromptInput | null>(null);

  const { data, isLoading, error } = useSWR<HealthCoverageResponse>(
    `code-health-coverage:${adapter.cacheKey}`,
    () => adapter.getCoverage({ limit: 1000 }),
    { revalidateOnFocus: false },
  );

  // The untested_hotspot findings carry dependents_count / commit_count_90d
  // in their details — join them into the warning entries.
  const { data: untestedFindings } = useSWR<HealthFinding[]>(
    `code-health-untested-findings:${adapter.cacheKey}`,
    () =>
      adapter
        .listFindings({ biomarker_type: "untested_hotspot", limit: 50 })
        .catch(() => []),
    { revalidateOnFocus: false },
  );

  const openFilePage = (path: string) =>
    adapter.navigate(`${adapter.fileHref(path)}?tab=coverage`);

  return (
    <div className="space-y-6">
      {isLoading ? (
        <CoverageSkeleton />
      ) : error ? (
        <EmptyState
          title="Couldn't load coverage data"
          description="The coverage endpoint returned an error. Try refreshing, or re-run the health pass."
        />
      ) : !data || data.summary.file_count === 0 ? (
        <NoCoverageState />
      ) : (
        <CoverageBody
          data={data}
          untestedFindings={untestedFindings ?? []}
          onOpenFile={openFilePage}
          onGeneratePrompt={(r) => setPromptRow(r)}
        />
      )}

      <AiPromptModal
        open={promptRow !== null}
        onOpenChange={(open) => {
          if (!open) setPromptRow(null);
        }}
        filePath={promptRow?.file_path ?? null}
        title="AI test prompt"
        description="A ready-to-paste prompt that asks your AI coding agent to add tests for this file's uncovered lines and branches."
        getPrompt={
          promptRow
            ? (flavor) => buildCoverageAiPrompt({ row: promptRow, flavor })
            : null
        }
      />
    </div>
  );
}

function CoverageBody({
  data,
  untestedFindings,
  onOpenFile,
  onGeneratePrompt,
}: {
  data: HealthCoverageResponse;
  untestedFindings: HealthFinding[];
  onOpenFile: (path: string) => void;
  onGeneratePrompt: (row: CoverageFilePromptInput) => void;
}) {
  const { summary, files, modules } = data;
  const [search, setSearch] = useState("");

  const num = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v) ? v : null;

  // Untested hotspots: prefer the real biomarker findings (which carry
  // dependents/commit context); fall back to the low-coverage × low-score
  // heuristic when the biomarker never fired (e.g. coverage just ingested).
  const untested = useMemo<UntestedHotspotEntry[]>(() => {
    const covByPath = new Map(files.map((f) => [f.file_path, f]));
    if (untestedFindings.length > 0) {
      return untestedFindings.slice(0, 10).map((f) => {
        const cov = covByPath.get(f.file_path);
        const d = f.details ?? {};
        const entry: UntestedHotspotEntry = {
          file_path: f.file_path,
          line_coverage_pct:
            num(d.line_coverage_pct) ?? cov?.line_coverage_pct ?? null,
          commit_count_90d: num(d.commit_count_90d),
        };
        const dependents = num(d.dependents_count);
        if (dependents != null) entry.dependents_count = dependents;
        if (cov?.health_score != null) entry.health_score = cov.health_score;
        return entry;
      });
    }
    return files
      .filter(
        (f) =>
          f.line_coverage_pct != null &&
          f.line_coverage_pct < 30 &&
          (f.health_score == null || f.health_score < 6),
      )
      .slice(0, 10)
      .map((f) => {
        const entry: UntestedHotspotEntry = {
          file_path: f.file_path,
          line_coverage_pct: f.line_coverage_pct,
        };
        if (f.health_score != null) entry.health_score = f.health_score;
        return entry;
      });
  }, [files, untestedFindings]);

  const scatterPoints = useMemo(
    () =>
      files
        .filter((f) => f.health_score != null && f.line_coverage_pct != null)
        .map((f) => ({
          file_path: f.file_path,
          health_score: f.health_score!,
          line_coverage_pct: f.line_coverage_pct,
          nloc: f.nloc ?? 0,
        })),
    [files],
  );

  const filteredFiles = useMemo(() => {
    if (!search) return files;
    const s = search.toLowerCase();
    return files.filter((f) => f.file_path.toLowerCase().includes(s));
  }, [files, search]);

  const columns: ResponsiveColumn<CoverageFileRow>[] = [
    {
      key: "file_path",
      header: "File",
      priority: 1,
      render: (f) => (
        <span className="inline-flex items-center gap-1.5 font-mono text-xs text-[var(--color-text-primary)]">
          <span className="truncate max-w-[420px]" title={f.file_path}>
            {f.file_path}
          </span>
          <ArrowUpRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
        </span>
      ),
    },
    {
      key: "line_coverage_pct",
      header: "Line coverage",
      priority: 1,
      render: (f) => <CoverageBar value={f.line_coverage_pct} size="sm" />,
    },
    {
      key: "branch_coverage_pct",
      header: "Branch",
      priority: 3,
      align: "right",
      render: (f) => (
        <span className="tabular-nums text-[var(--color-text-secondary)]">
          {f.branch_coverage_pct == null ? "—" : `${f.branch_coverage_pct.toFixed(0)}%`}
        </span>
      ),
    },
    {
      key: "total_coverable_lines",
      header: "Lines",
      priority: 3,
      align: "right",
      render: (f) => (
        <span className="tabular-nums text-[var(--color-text-tertiary)]">
          {f.total_coverable_lines}
        </span>
      ),
    },
    {
      key: "health_score",
      header: "Health",
      priority: 2,
      align: "right",
      render: (f) =>
        f.health_score == null ? (
          <span className="text-[var(--color-text-tertiary)]">—</span>
        ) : (
          <span
            className={`inline-block rounded px-1.5 py-0.5 text-xs font-semibold ${scoreBadgeClass(f.health_score)}`}
          >
            {f.health_score.toFixed(1)}
          </span>
        ),
    },
    {
      key: "actions",
      header: "",
      priority: 1,
      align: "right",
      render: (f) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onGeneratePrompt({
              file_path: f.file_path,
              line_coverage_pct: f.line_coverage_pct,
              branch_coverage_pct: f.branch_coverage_pct,
              total_coverable_lines: f.total_coverable_lines,
              ...(f.covered_lines && { covered_lines: f.covered_lines }),
              source_format: f.source_format,
              health_score: f.health_score ?? null,
              nloc: f.nloc ?? null,
            });
          }}
          title="Generate AI test prompt for this file"
          className="inline-flex items-center justify-center rounded-md p-1 text-[var(--color-text-tertiary)] hover:text-[var(--color-success)] hover:bg-[var(--color-success)]/10 transition-colors"
        >
          <Sparkles className="h-3.5 w-3.5" />
        </button>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        <MetricCard label="Files" value={summary.file_count.toLocaleString()} />
        <MetricCard
          label="Line coverage"
          value={
            summary.line_coverage_pct == null
              ? "—"
              : `${summary.line_coverage_pct.toFixed(1)}%`
          }
          distBar={
            <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
              {summary.covered_lines.toLocaleString()} /{" "}
              {summary.total_lines.toLocaleString()} lines
            </span>
          }
        />
        <MetricCard
          label="Branch coverage"
          value={
            summary.branch_coverage_pct == null
              ? "—"
              : `${summary.branch_coverage_pct.toFixed(1)}%`
          }
        />
        <MetricCard
          label="Source"
          value={
            <span className="text-lg uppercase">{summary.source_format ?? "—"}</span>
          }
          distBar={
            <span className="text-xs text-[var(--color-text-tertiary)]">
              {summary.ingested_at
                ? new Date(summary.ingested_at).toLocaleString()
                : "never"}
            </span>
          }
        />
      </div>

      <RiskCoverageScatter points={scatterPoints} onSelect={(p) => onOpenFile(p.file_path)} />

      {untested.length > 0 ? <UntestedHotspotWarning entries={untested} /> : null}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
          Module coverage ({modules.length})
        </h2>
        <ModuleCoverageList modules={modules} />
      </section>

      <CollapsibleSection
        title={`Files (${filteredFiles.length.toLocaleString()})`}
        defaultOpen={false}
      >
        <div className="flex items-center gap-2">
          <span className="mr-auto text-xs text-[var(--color-text-tertiary)]">
            Per-file coverage — click a row for the line-level heatmap.
          </span>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter path…"
            className="text-xs px-2 py-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] w-56 focus:outline-none focus:border-[var(--color-border-strong)]"
          />
        </div>
        <ResponsiveTable
          columns={columns}
          rows={filteredFiles}
          rowKey={(f) => f.file_path}
          onRowClick={(f) => onOpenFile(f.file_path)}
          stacked="sm"
          empty={
            <EmptyState
              title="No files match"
              description="Adjust the path filter to see coverage rows."
            />
          }
        />
      </CollapsibleSection>
    </div>
  );
}

function NoCoverageState() {
  return (
    <div className="rounded-lg border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-8">
      <div className="max-w-xl mx-auto text-center space-y-3">
        <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
          No coverage data ingested yet
        </h2>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Run your test suite with coverage enabled, then ingest the report.
          You&apos;ll see a risk × coverage map, untested-hotspot warnings, and a
          per-module breakdown.
        </p>
        <pre className="inline-block px-4 py-2 rounded bg-[var(--color-bg-muted)] text-left text-xs font-mono">
          pytest --cov --cov-report=lcov{"\n"}
          repowise health --coverage coverage.lcov
        </pre>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Supported formats: LCOV · Cobertura · Clover.
        </p>
      </div>
    </div>
  );
}

function CoverageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-24 w-full rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-72 w-full rounded-lg" />
      <Skeleton className="h-48 w-full rounded-lg" />
    </div>
  );
}
