"use client";

import { useMemo, useState } from "react";
import { ArrowUpRight, Sparkles, TestTubeDiagonal } from "lucide-react";
import { useParams } from "next/navigation";
import useSWR from "swr";

import {
  AiPromptModal,
  CoverageBar,
  ModuleCoverageList,
  UntestedHotspotWarning,
  RiskCoverageScatter,
  buildCoverageAiPrompt,
  scoreBadgeClass,
  type CoverageFilePromptInput,
} from "@repowise-dev/ui/health";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

import {
  getHealthCoverage,
  type HealthCoverageResponse,
  getHealthOverview,
  type HealthOverviewResponse,
} from "@/lib/api/code-health";
import { HealthPageChrome } from "@/components/health/health-page-chrome";
import { HealthFileDrawerHost } from "@/components/health/health-file-drawer-host";

export default function HealthCoveragePage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [promptRow, setPromptRow] = useState<CoverageFilePromptInput | null>(null);

  const { data, isLoading, error } = useSWR<HealthCoverageResponse>(
    `code-health-coverage:${id}`,
    () => getHealthCoverage(id, { limit: 1000 }),
    { revalidateOnFocus: false },
  );

  const { data: overview } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${id}`,
    () => getHealthOverview(id, 25),
    { revalidateOnFocus: false },
  );

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <HealthPageChrome
        repoId={id}
        active="coverage"
        basePath={`/repos/${id}/code-health`}
        title="Test Coverage"
        icon={<TestTubeDiagonal className="h-5 w-5 text-emerald-500" />}
        subtitle={
          <>
            Ingest LCOV / Cobertura / Clover with{" "}
            <code className="px-1 rounded bg-[var(--color-bg-muted)]">
              repowise health --coverage &lt;path&gt;
            </code>
          </>
        }
        meta={overview?.meta}
      />

      {isLoading ? (
        <CoverageSkeleton />
      ) : error ? (
        <p className="text-sm text-red-500">Failed to load coverage data.</p>
      ) : !data || data.summary.file_count === 0 ? (
        <EmptyState />
      ) : (
        <CoverageView
          data={data}
          onSelect={(p) => setSelectedFile(p)}
          selectedFile={selectedFile}
          onGeneratePrompt={(r) => setPromptRow(r)}
        />
      )}

      <HealthFileDrawerHost
        repoId={id}
        filePath={selectedFile}
        onClose={() => setSelectedFile(null)}
      />

      <AiPromptModal
        open={promptRow !== null}
        onOpenChange={(open) => {
          if (!open) setPromptRow(null);
        }}
        filePath={promptRow?.file_path}
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

function CoverageView({
  data,
  onSelect,
  selectedFile,
  onGeneratePrompt,
}: {
  data: HealthCoverageResponse;
  onSelect: (path: string) => void;
  selectedFile: string | null;
  onGeneratePrompt: (row: CoverageFilePromptInput) => void;
}) {
  const { summary, files, modules } = data;
  const [search, setSearch] = useState("");

  // Only flag a file as "untested hotspot" when it actually has coverage
  // data — defaulting missing coverage to 100% would hide the genuinely
  // uncovered files. We pair the health score and coverage so the chip
  // only fires on real risk-and-uncovered overlap.
  const untested = useMemo(
    () =>
      files
        .filter(
          (f) =>
            f.line_coverage_pct != null &&
            f.line_coverage_pct < 30 &&
            (f.health_score == null || f.health_score < 6),
        )
        .slice(0, 10)
        .map((f) => ({
          file_path: f.file_path,
          line_coverage_pct: f.line_coverage_pct,
          health_score: f.health_score,
        })),
    [files],
  );

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

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        <SummaryCard label="Files">
          <span className="text-2xl font-bold tabular-nums text-[var(--color-text-primary)]">
            {summary.file_count.toLocaleString()}
          </span>
        </SummaryCard>
        <SummaryCard label="Line coverage">
          <span className="text-2xl font-bold tabular-nums text-[var(--color-text-primary)]">
            {summary.line_coverage_pct == null
              ? "—"
              : `${summary.line_coverage_pct.toFixed(1)}%`}
          </span>
          <p className="text-xs text-[var(--color-text-tertiary)] mt-1 tabular-nums">
            {summary.covered_lines.toLocaleString()} /{" "}
            {summary.total_lines.toLocaleString()} lines
          </p>
        </SummaryCard>
        <SummaryCard label="Branch coverage">
          <span className="text-2xl font-bold tabular-nums text-[var(--color-text-primary)]">
            {summary.branch_coverage_pct == null
              ? "—"
              : `${summary.branch_coverage_pct.toFixed(1)}%`}
          </span>
        </SummaryCard>
        <SummaryCard label="Source">
          <span className="text-lg font-semibold text-[var(--color-text-primary)] uppercase">
            {summary.source_format ?? "—"}
          </span>
          <p className="text-xs text-[var(--color-text-tertiary)] mt-1">
            {summary.ingested_at ? new Date(summary.ingested_at).toLocaleString() : "never"}
          </p>
        </SummaryCard>
      </div>

      <RiskCoverageScatter points={scatterPoints} onSelect={(p) => onSelect(p.file_path)} />

      {untested.length > 0 ? <UntestedHotspotWarning entries={untested} /> : null}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
          Module coverage ({modules.length})
        </h2>
        <ModuleCoverageList modules={modules} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mr-auto">
            Files ({filteredFiles.length.toLocaleString()})
          </h2>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter path…"
            className="text-xs px-2 py-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] w-56 focus:outline-none focus:border-[var(--color-border-strong)]"
          />
        </div>
        <FileCoverageTable
          files={filteredFiles}
          onSelect={onSelect}
          selectedFile={selectedFile}
          onGeneratePrompt={onGeneratePrompt}
        />
      </section>
    </div>
  );
}

function FileCoverageTable({
  files,
  onSelect,
  selectedFile,
  onGeneratePrompt,
}: {
  files: HealthCoverageResponse["files"];
  onSelect: (path: string) => void;
  selectedFile: string | null;
  onGeneratePrompt: (row: CoverageFilePromptInput) => void;
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] overflow-hidden max-h-[600px] overflow-y-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-[var(--color-border-default)] text-xs uppercase tracking-wide text-[var(--color-text-tertiary)] bg-[var(--color-bg-elevated)] sticky top-0">
          <tr>
            <th className="px-4 py-2 text-left font-medium">File</th>
            <th className="px-4 py-2 text-left font-medium w-64">Line coverage</th>
            <th className="px-4 py-2 text-right font-medium">Branch</th>
            <th className="px-4 py-2 text-right font-medium">Lines</th>
            <th className="px-4 py-2 text-right font-medium">Health</th>
            <th className="px-4 py-2 text-right font-medium w-12"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border-default)]">
          {files.map((f) => {
            const isSelected = selectedFile === f.file_path;
            return (
              <tr
                key={f.file_path}
                className={`cursor-pointer hover:bg-[var(--color-bg-muted)] ${isSelected ? "bg-[var(--color-accent-muted)]/20" : ""}`}
                onClick={() => onSelect(f.file_path)}
              >
                <td className="px-4 py-2 font-mono text-xs text-[var(--color-text-primary)] truncate max-w-[480px]" title={f.file_path}>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="truncate">{f.file_path}</span>
                    <ArrowUpRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
                  </span>
                </td>
                <td className="px-4 py-2">
                  <CoverageBar value={f.line_coverage_pct} size="sm" />
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">
                  {f.branch_coverage_pct == null
                    ? "—"
                    : `${f.branch_coverage_pct.toFixed(0)}%`}
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-[var(--color-text-tertiary)]">
                  {f.total_coverable_lines}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {f.health_score == null ? (
                    <span className="text-[var(--color-text-tertiary)]">—</span>
                  ) : (
                    <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-semibold ${scoreBadgeClass(f.health_score)}`}>
                      {f.health_score.toFixed(1)}
                    </span>
                  )}
                </td>
                <td className="px-2 py-2 text-right">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onGeneratePrompt({
                        file_path: f.file_path,
                        line_coverage_pct: f.line_coverage_pct,
                        branch_coverage_pct: f.branch_coverage_pct,
                        total_coverable_lines: f.total_coverable_lines,
                        covered_lines: f.covered_lines,
                        source_format: f.source_format,
                        health_score: f.health_score ?? null,
                        nloc: f.nloc ?? null,
                      });
                    }}
                    title="Generate AI test prompt for this file"
                    className="inline-flex items-center justify-center rounded-md p-1 text-[var(--color-text-tertiary)] hover:text-emerald-500 hover:bg-emerald-500/10 transition-colors"
                  >
                    <Sparkles className="h-3.5 w-3.5" />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SummaryCard({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
      <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1">
        {label}
      </p>
      {children}
    </div>
  );
}

function EmptyState() {
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
      <div className="grid grid-cols-4 gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-72 w-full rounded-lg" />
      <Skeleton className="h-48 w-full rounded-lg" />
    </div>
  );
}
