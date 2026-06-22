import * as React from "react";
import {
  FileText,
  GitCommitHorizontal,
  Hourglass,
  Network,
  Workflow,
  Boxes,
} from "lucide-react";
import type { StatsSuperlatives } from "@repowise-dev/types/stats";
import { formatNumber, formatRelativeTimeOrNull, truncatePath } from "../lib/format";

interface AwardRow {
  key: string;
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  primary: string;
  detail: string;
}

function buildAwards(s: StatsSuperlatives): AwardRow[] {
  const rows: AwardRow[] = [];
  if (s.largest_file) {
    rows.push({
      key: "largest",
      icon: FileText,
      title: "Largest file",
      primary: truncatePath(s.largest_file.path, 42),
      detail: `${formatNumber(s.largest_file.nloc)} lines`,
    });
  }
  if (s.most_complex_symbol) {
    rows.push({
      key: "complex",
      icon: Workflow,
      title: "Most complex symbol",
      primary: s.most_complex_symbol.name,
      detail: `complexity ${formatNumber(s.most_complex_symbol.complexity)} · ${truncatePath(
        s.most_complex_symbol.file_path,
        32,
      )}`,
    });
  }
  if (s.most_changed_file) {
    rows.push({
      key: "changed",
      icon: GitCommitHorizontal,
      title: "Most-changed file",
      primary: truncatePath(s.most_changed_file.path, 42),
      detail: `${formatNumber(s.most_changed_file.commit_count)} commits`,
    });
  }
  if (s.oldest_file) {
    rows.push({
      key: "oldest",
      icon: Hourglass,
      title: "Oldest file",
      primary: truncatePath(s.oldest_file.path, 42),
      detail: `first commit ${formatRelativeTimeOrNull(s.oldest_file.first_commit_at)}`,
    });
  }
  if (s.most_central_file) {
    rows.push({
      key: "central",
      icon: Network,
      title: "Most central file",
      primary: truncatePath(s.most_central_file.path, 42),
      detail: `PageRank ${s.most_central_file.pagerank.toFixed(4)}`,
    });
  }
  if (s.strongest_coupling) {
    rows.push({
      key: "coupling",
      icon: Boxes,
      title: "Strongest hidden coupling",
      primary: `${truncatePath(s.strongest_coupling.a, 26)} ↔ ${truncatePath(
        s.strongest_coupling.b,
        26,
      )}`,
      detail: `changed together ${formatNumber(s.strongest_coupling.count)}×`,
    });
  }
  return rows;
}

interface SuperlativesGridProps {
  superlatives: StatsSuperlatives;
}

/** A grid of "award" cards — the biggest / oldest / most-tangled records in
 *  the repo. Renders only the awards that have data. */
export function SuperlativesGrid({ superlatives }: SuperlativesGridProps) {
  const awards = buildAwards(superlatives);
  if (awards.length === 0) return null;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {awards.map((a) => {
        const Icon = a.icon;
        return (
          <div
            key={a.key}
            className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4"
          >
            <div className="flex items-center gap-2 text-[var(--color-text-tertiary)]">
              <Icon className="h-4 w-4" />
              <span className="text-[11px] font-medium uppercase tracking-wider">{a.title}</span>
            </div>
            <p
              className="mt-2 truncate text-sm font-semibold text-[var(--color-text-primary)]"
              title={a.primary}
            >
              {a.primary}
            </p>
            <p className="mt-0.5 truncate text-xs text-[var(--color-text-secondary)]" title={a.detail}>
              {a.detail}
            </p>
          </div>
        );
      })}
    </div>
  );
}
