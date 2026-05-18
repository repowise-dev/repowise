import { HealthBadge } from "./health-badge.js";

export interface ModuleRollupRow {
  module: string;
  file_count: number;
  nloc: number;
  average_health: number;
  worst_performer_path: string;
  worst_performer_score: number;
}

export interface ModuleRollupListProps {
  modules: ModuleRollupRow[];
  emptyMessage?: string;
}

export function ModuleRollupList({
  modules,
  emptyMessage = "No modules detected yet — community labels populate after the first `repowise init`.",
}: ModuleRollupListProps) {
  if (!modules || modules.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)]">
        {emptyMessage}
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider">
          <tr>
            <th className="text-left px-3 py-2">Module</th>
            <th className="text-left px-3 py-2">Files</th>
            <th className="text-left px-3 py-2">NLOC</th>
            <th className="text-left px-3 py-2">Avg health</th>
            <th className="text-left px-3 py-2">Worst file</th>
          </tr>
        </thead>
        <tbody>
          {modules.map((m) => (
            <tr
              key={m.module}
              className="border-t border-[var(--color-border-default)]"
            >
              <td className="px-3 py-2 font-medium text-[var(--color-text-primary)]">
                {m.module}
              </td>
              <td className="px-3 py-2 tabular-nums text-[var(--color-text-secondary)]">
                {m.file_count}
              </td>
              <td className="px-3 py-2 tabular-nums text-[var(--color-text-secondary)]">
                {m.nloc.toLocaleString()}
              </td>
              <td className="px-3 py-2">
                <HealthBadge score={m.average_health} />
              </td>
              <td className="px-3 py-2 font-mono text-xs text-[var(--color-text-secondary)] truncate max-w-[280px]">
                {m.worst_performer_path}{" "}
                <span className="text-[var(--color-text-tertiary)]">
                  ({m.worst_performer_score.toFixed(1)})
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
