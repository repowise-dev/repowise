"use client";

import * as React from "react";
import { Trash2, ArrowRight, Sparkles } from "lucide-react";
import { cn } from "../lib/cn";

export interface SafeToDeletePileFinding {
  id: string;
  file_path: string;
  symbol_name: string | null;
  lines: number;
  confidence: number;
}

interface SafeToDeletePileProps {
  findings: SafeToDeletePileFinding[];
  /** Total lines reclaimable across all safe findings (may differ if filtered). */
  reclaimableLines?: number;
  /** Optional CTA — invoked with the list of finding ids. */
  onPropose?: (findingIds: string[]) => void;
  /**
   * Optional click handler for individual rows — typically opens a context
   * drawer pre-loaded for that file or symbol.
   */
  onSelect?: (finding: SafeToDeletePileFinding) => void;
  className?: string;
}

/**
 * Big visual card highlighting the high-confidence "safe to delete" pile —
 * the demo's punchline for dead-code: "X lines, Y files, click to clean."
 */
export function SafeToDeletePile({
  findings,
  reclaimableLines,
  onPropose,
  onSelect,
  className,
}: SafeToDeletePileProps) {
  const lines =
    reclaimableLines ??
    findings.reduce((sum, f) => sum + (Number.isFinite(f.lines) ? f.lines : 0), 0);
  const files = new Set(findings.map((f) => f.file_path)).size;

  // Roll findings up by file so the preview list shows distinct files
  // instead of repeating the same path once per finding. Each group keeps
  // a pointer back to its largest finding so onSelect still opens
  // something meaningful in the context drawer.
  type FileGroup = {
    file_path: string;
    lines: number;
    finding_count: number;
    representative: SafeToDeletePileFinding;
  };
  const groups: FileGroup[] = React.useMemo(() => {
    const byFile = new Map<string, FileGroup>();
    for (const f of findings) {
      const existing = byFile.get(f.file_path);
      if (existing) {
        existing.lines += Number.isFinite(f.lines) ? f.lines : 0;
        existing.finding_count += 1;
        if ((f.lines ?? 0) > (existing.representative.lines ?? 0)) {
          existing.representative = f;
        }
      } else {
        byFile.set(f.file_path, {
          file_path: f.file_path,
          lines: Number.isFinite(f.lines) ? f.lines : 0,
          finding_count: 1,
          representative: f,
        });
      }
    }
    return [...byFile.values()].sort((a, b) => b.lines - a.lines);
  }, [findings]);
  const top = groups.slice(0, 5);
  const moreFiles = Math.max(0, groups.length - top.length);

  return (
    <div
      className={cn(
        "rounded-xl border border-rose-500/30 bg-gradient-to-br from-rose-500/10 via-[var(--color-bg-elevated)] to-[var(--color-bg-elevated)]",
        "p-5 shadow-sm",
        className,
      )}
    >
      <div className="flex items-start gap-4">
        <div className="rounded-lg bg-rose-500/15 p-2.5 text-rose-400 shrink-0">
          <Trash2 className="h-5 w-5" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-3xl font-semibold tabular-nums text-[var(--color-text-primary)]">
              {lines.toLocaleString()}
            </span>
            <span className="text-sm text-[var(--color-text-secondary)]">lines safe to delete</span>
          </div>
          <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            High-confidence findings across <span className="font-medium">{files}</span> file
            {files === 1 ? "" : "s"} ({findings.length} finding{findings.length === 1 ? "" : "s"}).
          </p>
        </div>
        {onPropose && findings.length > 0 && (
          <button
            type="button"
            onClick={() => onPropose(findings.map((f) => f.id))}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-1.5",
              "text-xs font-medium text-rose-300 transition hover:bg-rose-500/20",
            )}
          >
            <Sparkles className="h-3.5 w-3.5" />
            Propose cleanup
          </button>
        )}
      </div>

      {top.length > 0 && (
        <ul className="mt-4 grid gap-1.5">
          {top.map((g) => {
            const Tag = onSelect ? "button" : "div";
            return (
              <li key={g.file_path}>
                <Tag
                  type={onSelect ? "button" : undefined}
                  onClick={onSelect ? () => onSelect(g.representative) : undefined}
                  className={cn(
                    "flex w-full items-center justify-between gap-3 rounded-md border border-transparent px-2 py-1.5",
                    "text-left text-xs transition",
                    onSelect && "hover:border-[var(--color-border-default)] hover:bg-[var(--color-bg-surface)]",
                  )}
                >
                  <span
                    className="font-mono text-[11px] text-[var(--color-text-secondary)] truncate"
                    title={g.file_path}
                  >
                    {g.file_path}
                    {g.finding_count > 1 && (
                      <span className="ml-1.5 text-[var(--color-text-tertiary)]">
                        ({g.finding_count} findings)
                      </span>
                    )}
                  </span>
                  <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-tertiary)]">
                    {g.lines.toLocaleString()} lines
                    {onSelect && <ArrowRight className="ml-1 inline h-3 w-3" />}
                  </span>
                </Tag>
              </li>
            );
          })}
          {moreFiles > 0 && (
            <li className="px-2 pt-0.5 text-[11px] text-[var(--color-text-tertiary)]">
              +{moreFiles.toLocaleString()} more file{moreFiles === 1 ? "" : "s"} —
              {" "}see findings table below.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
