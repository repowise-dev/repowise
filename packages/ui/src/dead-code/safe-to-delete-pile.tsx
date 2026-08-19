"use client";

import * as React from "react";
import { ArrowRight } from "lucide-react";
import { AiPromptButton } from "../health/ai-prompt-button";
import { OverviewSection } from "../overview/section";
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
 * The "what do I delete" list: files carrying high-confidence findings, biggest
 * pile first.
 *
 * It used to be a red gradient card with a trash icon in a tinted tile and the
 * reclaimable line count set at 3xl. The lede above now leads with that same
 * figure, so repeating it here at near-hero size gave the page two headlines
 * that agree, and the red ground made a list of file paths read as a warning
 * about the files rather than an offer to remove them. Rule 9: the loudest
 * thing on screen should be the thing that responds, and what responds here is
 * the row and the prompt button.
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
  const top = groups.slice(0, 6);
  const moreFiles = Math.max(0, groups.length - top.length);

  return (
    <OverviewSection
      title="Safe to delete"
      description={`${lines.toLocaleString()} lines across ${files.toLocaleString()} file${files === 1 ? "" : "s"} (${findings.length.toLocaleString()} finding${findings.length === 1 ? "" : "s"}) come back high confidence, with no caller we can find. Read the diff before you delete, then hand the rest to an agent.`}
      {...(className ? { className } : {})}
      action={
        onPropose && findings.length > 0 ? (
          // The canonical AI affordance, not a bespoke red button: every other
          // "hand this to an agent" action in the dashboard is this pill, and
          // red read as destructive on a button that only writes a prompt.
          <AiPromptButton
            label="Propose cleanup"
            onClick={() => onPropose(findings.map((f) => f.id))}
          />
        ) : undefined
      }
    >
      {top.length > 0 && (
        <ul className="border-t border-[var(--color-border-default)]">
          {top.map((g) => {
            const Tag = onSelect ? "button" : "div";
            return (
              <li
                key={g.file_path}
                className="border-b border-[var(--color-border-default)]"
              >
                <Tag
                  type={onSelect ? "button" : undefined}
                  onClick={onSelect ? () => onSelect(g.representative) : undefined}
                  className={cn(
                    "flex w-full items-center justify-between gap-3 py-2.5 text-left text-xs",
                    onSelect && "hover:bg-[var(--color-bg-elevated)]",
                  )}
                >
                  <span
                    className="min-w-0 truncate font-mono text-xs text-[var(--color-text-primary)]"
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
        </ul>
      )}
      {moreFiles > 0 && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {moreFiles.toLocaleString()} more file{moreFiles === 1 ? "" : "s"} in the
          findings table below.
        </p>
      )}
    </OverviewSection>
  );
}
