import { CoverageBar } from "./coverage-bar";

export interface ModuleCoverageRow {
  module: string;
  files: number;
  covered_lines: number;
  total_lines: number;
  line_coverage_pct: number;
}

export interface ModuleCoverageListProps {
  modules: ModuleCoverageRow[];
}

export function ModuleCoverageList({ modules }: ModuleCoverageListProps) {
  if (modules.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)]">
        No coverage data for any module.
      </p>
    );
  }
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] divide-y divide-[var(--color-border-default)]">
      {modules.map((m) => (
        <div key={m.module} className="px-4 py-3">
          <div className="flex items-center justify-between gap-3 mb-1.5">
            <span className="font-mono text-sm text-[var(--color-text-primary)] truncate">
              {m.module}
            </span>
            <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums">
              {m.files} files · {m.covered_lines}/{m.total_lines} lines
            </span>
          </div>
          <CoverageBar value={m.line_coverage_pct} />
        </div>
      ))}
    </div>
  );
}
