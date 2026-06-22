"use client";

import { Badge } from "../ui/badge";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import { ChevronRight } from "lucide-react";

export interface RepoPairSummary {
  id: string; // "repo1↔repo2"
  repo1: string;
  repo2: string;
  filePairCount: number;
  maxStrength: number;
  lastDate: string;
}

interface RepoPairTableProps {
  repoPairs: RepoPairSummary[];
  onSelectPair?: (id: string) => void;
  selectedPairId?: string | null;
}

export function RepoPairTable({ repoPairs, onSelectPair, selectedPairId }: RepoPairTableProps) {
  const columns: ResponsiveColumn<RepoPairSummary>[] = [
    {
      key: "pair",
      header: "Repository Pair",
      priority: 1,
      render: (p) => (
        <div className="flex items-center gap-2">
          <Badge variant="default" className="text-xs">{p.repo1}</Badge>
          <span className="text-[var(--color-text-tertiary)] text-xs">↔</span>
          <Badge variant="default" className="text-xs">{p.repo2}</Badge>
        </div>
      ),
    },
    {
      key: "count",
      header: "File Pairs",
      priority: 2,
      align: "right",
      cellClassName: "text-xs text-[var(--color-text-secondary)] tabular-nums",
      render: (p) => p.filePairCount,
    },
    {
      key: "strength",
      header: "Max Strength",
      priority: 1,
      headerClassName: "w-32",
      render: (p) => (
        <div className="flex items-center gap-2 min-w-[90px]">
          <div className="h-1.5 flex-1 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
            <div
              className="h-full rounded-full bg-[var(--color-accent-primary)] transition-all"
              style={{ width: `${Math.min(Math.round(p.maxStrength * 10), 100)}%` }}
            />
          </div>
          <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8 text-right">
            {Math.round(p.maxStrength * 10) / 10}
          </span>
        </div>
      ),
      mobileRender: (p) => Math.round(p.maxStrength * 10) / 10,
    },
    {
      key: "last",
      header: "Latest Activity",
      priority: 3,
      align: "right",
      cellClassName: "text-xs text-[var(--color-text-tertiary)]",
      render: (p) => (
        <span title={p.lastDate ? new Date(p.lastDate).toLocaleString() : undefined}>
          {p.lastDate ? new Date(p.lastDate).toLocaleDateString() : "—"}
        </span>
      ),
    },
    ...(onSelectPair
      ? [
          {
            key: "action",
            header: "",
            priority: 1 as const,
            align: "right" as const,
            render: () => <ChevronRight className="h-4 w-4 text-[var(--color-text-tertiary)] inline-block" />,
            hideInCard: true,
          },
        ]
      : []),
  ];

  return (
    <ResponsiveTable
      columns={columns}
      rows={repoPairs}
      rowKey={(p) => p.id}
      selectedKey={selectedPairId}
      onRowClick={onSelectPair ? (p) => onSelectPair(p.id) : undefined}
      bare
      empty={
        <EmptyState
          title="No repository pairs"
          description="No cross-repository co-changes found."
        />
      }
    />
  );
}
