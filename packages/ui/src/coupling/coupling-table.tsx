"use client";

import { useMemo } from "react";
import { EmptyState } from "../shared/empty-state";
import { VirtualizedTable } from "../shared/virtualized-table";
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

// Column-priority hide classes, mirroring the shared ResponsiveTable scale:
// priority 2 hides below md (768px), priority 3 hides below lg (1024px). The
// "pair" identity column (priority 1) is always visible.
const HIDE_BELOW_MD = "max-md:hidden";
const HIDE_BELOW_LG = "max-lg:hidden";

/**
 * The precise, sortable companion to the coupling diagram: one row per
 * coupling, strongest first. Clicking a row focuses that file in the ring;
 * rows touching the focused file are emphasized.
 *
 * The body is virtualized (windowed `<tbody>`) so long coupling lists stay
 * cheap to render; below the wrapper's threshold every row renders, so the
 * common short list behaves exactly as a plain table.
 */
export function CouplingTable({ edges, focusedPath, onFocusChange, onGeneratePrompt }: CouplingTableProps) {
  // The strength bars are normalized to the strongest coupling; recompute only
  // when the edge list changes rather than on every render.
  const maxStrength = useMemo(() => Math.max(...edges.map((e) => e.strength), 1), [edges]);

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

  if (edges.length === 0) {
    return (
      <EmptyState
        title="No couplings detected"
        description="No files in this repository have a history of changing together yet."
      />
    );
  }

  const header = (
    <tr className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider">
      <th className="px-3 py-2 text-left font-medium whitespace-nowrap min-w-[200px]">
        Coupled files
      </th>
      <th className={`px-3 py-2 text-left font-medium whitespace-nowrap w-36 ${HIDE_BELOW_MD}`}>
        <span
          title="Recency-weighted count of commits that touched both files. Higher means more or more-recent shared changes. It is not a percentage or a verified dependency."
          className="cursor-help underline decoration-dotted underline-offset-2"
        >
          Strength
        </span>
      </th>
      <th className={`px-3 py-2 text-left font-medium whitespace-nowrap ${HIDE_BELOW_LG}`}>Last</th>
      {onGeneratePrompt ? (
        <th className={`px-3 py-2 text-left font-medium whitespace-nowrap w-10 ${HIDE_BELOW_LG}`} />
      ) : null}
    </tr>
  );

  const renderRow = (e: CouplingEdge) => {
    const onClick = onFocusChange
      ? () => onFocusChange(focusedPath === e.source ? null : e.source)
      : undefined;
    const isSelected = focusedPath != null && focusedPath === e.source;
    return (
      <tr
        className={`border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)] ${
          isSelected ? "bg-[var(--color-accent-muted)]/30 " : ""
        }${onClick ? "cursor-pointer" : ""}`}
        onClick={onClick}
      >
        <td className="px-3 py-2 text-left min-w-[200px]">
          <div className="flex flex-col gap-0.5">
            {fileCell(e.source, e)}
            {fileCell(e.target, e, "↔ ")}
          </div>
        </td>
        <td className={`px-3 py-2 text-left ${HIDE_BELOW_MD}`}>
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
        </td>
        <td className={`px-3 py-2 text-left text-xs text-[var(--color-text-tertiary)] ${HIDE_BELOW_LG}`}>
          <span title={e.last_co_change ? new Date(e.last_co_change).toLocaleString() : undefined}>
            {e.last_co_change ? new Date(e.last_co_change).toLocaleDateString() : "—"}
          </span>
        </td>
        {onGeneratePrompt ? (
          <td className={`px-3 py-2 text-left w-10 ${HIDE_BELOW_LG}`}>
            <AiPromptButton
              variant="icon"
              label="AI decouple prompt"
              onClick={() => onGeneratePrompt(e)}
            />
          </td>
        ) : null}
      </tr>
    );
  };

  return (
    <VirtualizedTable<CouplingEdge>
      rows={edges}
      rowKey={(e) => `${e.source}|${e.target}`}
      header={header}
      renderRow={renderRow}
      aria-label="Change-coupling pairs"
    />
  );
}
