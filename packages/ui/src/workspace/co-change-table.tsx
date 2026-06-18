"use client";

import { Badge } from "../ui/badge";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";

interface CoChangeTableProps {
  coChanges: WorkspaceCoChangeEntry[];
  compact?: boolean;
}

export function CoChangeTable({ coChanges, compact }: CoChangeTableProps) {
  const columns: ResponsiveColumn<WorkspaceCoChangeEntry>[] = [
    {
      key: "source",
      header: "Source",
      priority: 1,
      cellClassName: "min-w-[160px] max-w-[280px]",
      render: (cc) => (
        <div className="flex flex-col gap-0.5">
          <Badge variant="default" className="w-fit text-xs">{cc.source_repo}</Badge>
          <span className="text-xs font-mono text-[var(--color-text-secondary)] truncate block" title={cc.source_file}>
            {cc.source_file}
          </span>
        </div>
      ),
    },
    {
      key: "target",
      header: "Target",
      priority: 1,
      cellClassName: "min-w-[160px] max-w-[280px]",
      render: (cc) => (
        <div className="flex flex-col gap-0.5">
          <Badge variant="default" className="w-fit text-xs">{cc.target_repo}</Badge>
          <span className="text-xs font-mono text-[var(--color-text-secondary)] truncate block" title={cc.target_file}>
            {cc.target_file}
          </span>
        </div>
      ),
    },
    {
      key: "strength",
      header: (
        <span
          title="Relative, recency-weighted frequency of same-author commits across these repos. Higher means more or more-recent shared activity. It is not a percentage or a verified dependency."
          className="cursor-help underline decoration-dotted underline-offset-2"
        >
          Strength
        </span>
      ),
      mobileLabel: "Strength",
      priority: 2,
      headerClassName: "w-32",
      render: (cc) => (
        <div className="flex items-center gap-2 min-w-[90px]">
          <div className="h-1.5 flex-1 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
            <div
              className="h-full rounded-full bg-[var(--color-accent-primary)] transition-all"
              style={{ width: `${Math.min(Math.round(cc.strength * 10), 100)}%` }}
            />
          </div>
          <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8 text-right">
            {Math.round(cc.strength * 10) / 10}
          </span>
        </div>
      ),
      mobileRender: (cc) => Math.round(cc.strength * 10) / 10,
    },
    ...(compact
      ? []
      : ([
          {
            key: "frequency",
            header: "Freq",
            align: "right",
            priority: 3,
            cellClassName: "text-xs text-[var(--color-text-secondary)] tabular-nums",
            render: (cc) => `${cc.frequency}x`,
          },
          {
            key: "last",
            header: "Last",
            priority: 3,
            cellClassName: "text-xs text-[var(--color-text-tertiary)]",
            render: (cc) => (
              <span title={cc.last_date ? new Date(cc.last_date).toLocaleString() : undefined}>
                {cc.last_date ? new Date(cc.last_date).toLocaleDateString() : "—"}
              </span>
            ),
          },
        ] satisfies ResponsiveColumn<WorkspaceCoChangeEntry>[])),
  ];

  return (
    <ResponsiveTable
      columns={columns}
      rows={coChanges}
      rowKey={(cc) => `${cc.source_repo}|${cc.source_file}|${cc.target_repo}|${cc.target_file}`}
      bare
      empty={
        <EmptyState
          title="No cross-repo co-changes"
          description="No files in sibling repos have changed together yet."
        />
      }
    />
  );
}
