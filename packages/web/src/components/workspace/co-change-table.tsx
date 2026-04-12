"use client";

import type { WorkspaceCoChangeEntry } from "@/lib/api/types";
import { Badge } from "@/components/ui/badge";

interface CoChangeTableProps {
  coChanges: WorkspaceCoChangeEntry[];
  compact?: boolean;
}

export function CoChangeTable({ coChanges, compact }: CoChangeTableProps) {
  if (coChanges.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)] py-4 text-center">
        No cross-repo co-changes detected.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border-default)] text-left text-xs text-[var(--color-text-tertiary)]">
            <th className="pb-2 pr-4 font-medium">Source</th>
            <th className="pb-2 pr-4 font-medium">Target</th>
            <th className="pb-2 pr-4 font-medium w-32">Strength</th>
            {!compact && <th className="pb-2 pr-4 font-medium">Freq</th>}
            {!compact && <th className="pb-2 font-medium">Last</th>}
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border-default)]">
          {coChanges.map((cc, i) => (
            <tr key={i} className="hover:bg-[var(--color-bg-elevated)] transition-colors">
              <td className="py-2 pr-4">
                <div className="flex flex-col gap-0.5">
                  <Badge variant="default" className="w-fit text-[11px]">{cc.source_repo}</Badge>
                  <span className="text-xs font-mono text-[var(--color-text-secondary)] truncate max-w-[250px]">
                    {cc.source_file}
                  </span>
                </div>
              </td>
              <td className="py-2 pr-4">
                <div className="flex flex-col gap-0.5">
                  <Badge variant="default" className="w-fit text-[11px]">{cc.target_repo}</Badge>
                  <span className="text-xs font-mono text-[var(--color-text-secondary)] truncate max-w-[250px]">
                    {cc.target_file}
                  </span>
                </div>
              </td>
              <td className="py-2 pr-4">
                <div className="flex items-center gap-2">
                  <div className="h-1.5 flex-1 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
                    <div
                      className="h-full rounded-full bg-[var(--color-accent-primary)] transition-all"
                      style={{ width: `${Math.round(cc.strength * 100)}%` }}
                    />
                  </div>
                  <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8 text-right">
                    {Math.round(cc.strength * 100)}%
                  </span>
                </div>
              </td>
              {!compact && (
                <td className="py-2 pr-4 text-xs text-[var(--color-text-secondary)] tabular-nums">
                  {cc.frequency}x
                </td>
              )}
              {!compact && (
                <td className="py-2 text-xs text-[var(--color-text-tertiary)]">
                  {cc.last_date ? new Date(cc.last_date).toLocaleDateString() : "—"}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
