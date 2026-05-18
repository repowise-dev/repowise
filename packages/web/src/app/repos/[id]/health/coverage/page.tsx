"use client";

import { TestTubeDiagonal } from "lucide-react";
import { useParams } from "next/navigation";
import useSWR from "swr";

import {
  CoverageBar,
  ModuleCoverageList,
  UntestedHotspotWarning,
} from "@repowise-dev/ui/health";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

import {
  getHealthCoverage,
  type HealthCoverageResponse,
} from "@/lib/api/code-health";

export default function HealthCoveragePage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const { data, isLoading, error } = useSWR<HealthCoverageResponse>(
    `code-health-coverage:${id}`,
    () => getHealthCoverage(id, { limit: 500 }),
    { revalidateOnFocus: false },
  );

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <TestTubeDiagonal className="h-5 w-5 text-emerald-500" />
          Test Coverage
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Ingest LCOV / Cobertura / Clover with{" "}
          <code className="px-1 rounded bg-[var(--color-bg-muted)]">
            repowise health --coverage &lt;path&gt;
          </code>
          .
        </p>
      </div>

      {isLoading ? (
        <CoverageSkeleton />
      ) : error ? (
        <p className="text-sm text-red-500">Failed to load coverage data.</p>
      ) : !data || data.summary.file_count === 0 ? (
        <EmptyState />
      ) : (
        <CoverageView data={data} />
      )}
    </div>
  );
}

function CoverageView({ data }: { data: HealthCoverageResponse }) {
  const { summary, files, modules } = data;
  const untested = files
    .filter(
      (f) =>
        (f.health_score != null && f.health_score < 5) ||
        (f.line_coverage_pct ?? 100) < 30,
    )
    .slice(0, 10)
    .map((f) => ({
      file_path: f.file_path,
      line_coverage_pct: f.line_coverage_pct,
      health_score: f.health_score,
    }));

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        <SummaryCard label="Files">
          <span className="text-2xl font-bold tabular-nums text-[var(--color-text-primary)]">
            {summary.file_count}
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

      <UntestedHotspotWarning entries={untested} />

      <section>
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-2">
          Module coverage
        </h2>
        <ModuleCoverageList modules={modules} />
      </section>

      <section>
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-2">
          Files
        </h2>
        <FileCoverageTable files={files} />
      </section>
    </div>
  );
}

function FileCoverageTable({
  files,
}: {
  files: HealthCoverageResponse["files"];
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] overflow-hidden">
      <table className="w-full text-sm">
        <thead className="border-b border-[var(--color-border-default)] text-xs uppercase tracking-wide text-[var(--color-text-tertiary)]">
          <tr>
            <th className="px-4 py-2 text-left font-medium">File</th>
            <th className="px-4 py-2 text-left font-medium w-64">Line coverage</th>
            <th className="px-4 py-2 text-right font-medium">Branch</th>
            <th className="px-4 py-2 text-right font-medium">Lines</th>
            <th className="px-4 py-2 text-right font-medium">Health</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border-default)]">
          {files.map((f) => (
            <tr key={f.file_path} className="hover:bg-[var(--color-bg-muted)]">
              <td className="px-4 py-2 font-mono text-xs text-[var(--color-text-primary)] truncate">
                {f.file_path}
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
              <td className="px-4 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">
                {f.health_score == null ? "—" : f.health_score.toFixed(1)}
              </td>
            </tr>
          ))}
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
    <div className="rounded-lg border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-8 text-center">
      <h2 className="text-base font-semibold text-[var(--color-text-primary)] mb-1">
        No coverage data ingested yet
      </h2>
      <p className="text-sm text-[var(--color-text-secondary)] mb-4">
        Run your test suite with coverage enabled, then ingest the report:
      </p>
      <pre className="inline-block px-4 py-2 rounded bg-[var(--color-bg-muted)] text-left text-xs font-mono">
        pytest --cov --cov-report=lcov{"\n"}
        repowise health --coverage coverage.lcov
      </pre>
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
      <Skeleton className="h-48 w-full rounded-lg" />
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  );
}
