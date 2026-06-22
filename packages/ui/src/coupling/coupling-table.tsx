"use client";

import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import { AiPromptButton } from "../health/ai-prompt-button";
import type { CouplingEdge } from "@repowise-dev/types/coupling";

interface CouplingTableProps {
  edges: CouplingEdge[];
  /** Focused file (emphasizes rows incident to it; synced with the diagram). */
  focusedPath?: string | null;
  onFocusChange?: (path: string | null) => void;
  /** When set, each row shows an "AI decouple prompt" action. */
  onGeneratePrompt?: (edge: CouplingEdge) => void;
}

function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

/**
 * The precise, sortable companion to the coupling diagram: one row per
 * coupling, strongest first. Clicking a row focuses that file in the ring;
 * rows touching the focused file are emphasized.
 */
export function CouplingTable({ edges, focusedPath, onFocusChange, onGeneratePrompt }: CouplingTableProps) {
  const maxStrength = Math.max(...edges.map((e) => e.strength), 1);
  const incident = (e: CouplingEdge) =>
    focusedPath != null && (e.source === focusedPath || e.target === focusedPath);

  const fileCell = (path: string, e: CouplingEdge, prefix = "") => {
    const hot = incident(e) && path === focusedPath;
    return (
      <span
        className={`block truncate font-mono text-xs ${
          hot ? "text-[var(--color-text-primary)] font-medium" : "text-[var(--color-text-secondary)]"
        }`}
        title={path}
      >
        {prefix}
        {basename(path)}
      </span>
    );
  };

  const columns: ResponsiveColumn<CouplingEdge>[] = [
    {
      key: "pair",
      header: "Coupled files",
      priority: 1,
      cellClassName: "min-w-[200px]",
      render: (e) => (
        <div className="flex flex-col gap-0.5">
          {fileCell(e.source, e)}
          {fileCell(e.target, e, "↔ ")}
        </div>
      ),
    },
    {
      key: "strength",
      header: (
        <span
          title="Recency-weighted count of commits that touched both files. Higher means more or more-recent shared changes. It is not a percentage or a verified dependency."
          className="cursor-help underline decoration-dotted underline-offset-2"
        >
          Strength
        </span>
      ),
      mobileLabel: "Strength",
      priority: 2,
      headerClassName: "w-36",
      render: (e) => (
        <div className="flex items-center gap-2 min-w-[100px]">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
            <div
              className="h-full rounded-full bg-[var(--color-accent-primary)]"
              style={{ width: `${Math.max(6, Math.round((e.strength / maxStrength) * 100))}%` }}
            />
          </div>
          <span className="w-8 text-right text-xs tabular-nums text-[var(--color-text-tertiary)]">
            {e.strength}
          </span>
        </div>
      ),
      mobileRender: (e) => e.strength,
    },
    {
      key: "last",
      header: "Last",
      priority: 3,
      cellClassName: "text-xs text-[var(--color-text-tertiary)]",
      render: (e) => (
        <span title={e.last_co_change ? new Date(e.last_co_change).toLocaleString() : undefined}>
          {e.last_co_change ? new Date(e.last_co_change).toLocaleDateString() : "—"}
        </span>
      ),
    },
    ...(onGeneratePrompt
      ? [
          {
            key: "ai",
            header: "",
            priority: 3,
            headerClassName: "w-10",
            render: (e: CouplingEdge) => (
              <AiPromptButton
                variant="icon"
                label="AI decouple prompt"
                onClick={() => onGeneratePrompt(e)}
              />
            ),
          } as ResponsiveColumn<CouplingEdge>,
        ]
      : []),
  ];

  return (
    <ResponsiveTable
      columns={columns}
      rows={edges}
      rowKey={(e) => `${e.source}|${e.target}`}
      bare
      onRowClick={
        onFocusChange ? (e) => onFocusChange(focusedPath === e.source ? null : e.source) : undefined
      }
      empty={
        <EmptyState
          title="No couplings detected"
          description="No files in this repository have a history of changing together yet."
        />
      }
    />
  );
}
