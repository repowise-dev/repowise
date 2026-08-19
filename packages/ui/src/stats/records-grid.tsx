import * as React from "react";
import {
  Boxes,
  FileText,
  GitCommitHorizontal,
  Hourglass,
  Network,
  Repeat,
  Ruler,
  ScrollText,
  Spline,
  Workflow,
  Zap,
} from "lucide-react";
import type { StatsRecords } from "@repowise-dev/types/stats";
import { formatNumber, formatRelativeTimeOrNull, truncatePath } from "../lib/format";

interface AwardRow {
  key: string;
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  primary: string;
  detail: string;
}

function buildAwards(s: StatsRecords): AwardRow[] {
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
  if (s.gnarliest_file) {
    rows.push({
      key: "gnarliest",
      icon: Spline,
      title: "Gnarliest file",
      primary: truncatePath(s.gnarliest_file.path, 42),
      detail: `cyclomatic complexity ${formatNumber(s.gnarliest_file.max_ccn)}`,
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
    const central = s.most_central_file;
    rows.push({
      key: "central",
      icon: Network,
      title: central.import_count != null ? "Most imported file" : "Most central file",
      primary: truncatePath(central.path, 42),
      detail:
        central.import_count != null
          ? `imported by ${formatNumber(central.import_count)} files · PageRank ${central.pagerank.toFixed(4)}`
          : `PageRank ${central.pagerank.toFixed(4)}`,
    });
  }
  if (s.biggest_commit) {
    rows.push({
      key: "biggest-commit",
      icon: Zap,
      title: "Biggest commit",
      primary: s.biggest_commit.subject || s.biggest_commit.sha.slice(0, 10),
      detail: `${formatNumber(s.biggest_commit.lines_changed)} lines across ${formatNumber(
        s.biggest_commit.files_changed,
      )} files — in one commit`,
    });
  }
  if (s.widest_commit) {
    rows.push({
      key: "widest-commit",
      icon: Ruler,
      title: "Widest commit",
      primary: s.widest_commit.subject || s.widest_commit.sha.slice(0, 10),
      detail: `touched ${formatNumber(s.widest_commit.files_changed)} files at once`,
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
      detail: `changed together ${formatNumber(Math.round(s.strongest_coupling.count))}×`,
    });
  }
  // Import cycles have no other surface in the app — Architecture visualises the
  // graph but never detects or counts circular imports.
  if (s.largest_cycle && s.largest_cycle.files > 1) {
    rows.push({
      key: "cycle",
      icon: Repeat,
      title: "Largest import cycle",
      primary: `${formatNumber(s.largest_cycle.files)} files in a loop`,
      detail: `${formatNumber(s.largest_cycle.cycle_count)} circular cluster${
        s.largest_cycle.cycle_count === 1 ? "" : "s"
      } in total`,
    });
  }
  if (s.symbol_shape && s.symbol_shape.total > 0) {
    const shape = s.symbol_shape;
    rows.push({
      key: "documented",
      icon: ScrollText,
      title: "Documented symbols",
      primary: `${Math.round(shape.documented_pct)}% carry a docstring`,
      detail: `${formatNumber(shape.documented_count)} of ${formatNumber(shape.total)} symbols · ${Math.round(
        shape.async_pct,
      )}% async`,
    });
  }
  return rows;
}

/**
 * The repo's records — biggest, oldest, gnarliest, most tangled.
 *
 * Several of these are the only headline treatment their signal gets anywhere:
 * cyclomatic complexity is otherwise a sortable column on Code Health, and
 * import cycles have no UI at all. Renders only awards that have data, so a
 * young repo shows a short grid rather than a wall of dashes.
 */
export function RecordsGrid({ records }: { records: StatsRecords }) {
  const awards = buildAwards(records);
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
            <p
              className="mt-0.5 truncate text-xs text-[var(--color-text-secondary)]"
              title={a.detail}
            >
              {a.detail}
            </p>
          </div>
        );
      })}
    </div>
  );
}
