"use client";

import { EmptyState } from "../shared/empty-state";
import { clickableRowProps, CLICKABLE_ROW_CLS } from "../shared/responsive-table";
import { AiPromptButton } from "../health/ai-prompt-button";
import { PAIR_MICRO as MICRO } from "../coupling/pair-drawer-parts";
import { cn } from "../lib/cn";
import { formatDate, formatDateTime } from "../lib/format";

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
  /** Opens the AI prompt for one repository pair. */
  onPrompt?: (pair: RepoPairSummary) => void;
}

// Priority 2 hides below md, priority 3 below lg, matching ResponsiveTable.
const HIDE_BELOW_MD = "max-md:hidden";
const HIDE_BELOW_LG = "max-lg:hidden";

/**
 * Cross-repo pair summary: one row per repository pair. Choosing a row narrows
 * the file list; the row says so, since a narrowing is not a navigation.
 *
 * Repository names are plain mono text rather than chips: they are neutral
 * categories, and a bordered chip reads as a control the row already is.
 */
export function RepoPairTable({ repoPairs, onSelectPair, selectedPairId, onPrompt }: RepoPairTableProps) {
  if (repoPairs.length === 0) {
    return (
      <EmptyState
        title="No repository pairs"
        description="No cross-repository co-changes found."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse" aria-label="Cross-repo pairs">
        <thead>
          <tr className="border-b border-[var(--color-border-default)]">
            <th className={cn(MICRO, "px-3 py-2 text-left font-normal")}>Repositories</th>
            <th className={cn(MICRO, "px-3 py-2 text-right font-normal", HIDE_BELOW_MD)}>File pairs</th>
            <th className={cn(MICRO, "px-3 py-2 text-right font-normal")}>Strongest</th>
            <th className={cn(MICRO, "px-3 py-2 text-right font-normal", HIDE_BELOW_LG)}>Latest</th>
            {onSelectPair || onPrompt ? (
              <th className="px-3 py-2">
                <span className="sr-only">Actions</span>
              </th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {repoPairs.map((p) => {
            const onClick = onSelectPair ? () => onSelectPair(p.id) : undefined;
            const isSelected = selectedPairId != null && selectedPairId === p.id;
            return (
              <tr
                key={p.id}
                className={cn(
                  "border-t border-[var(--color-border-default)] first:border-t-0",
                  onClick && "hover:bg-[var(--color-bg-elevated)]",
                  isSelected && "bg-[var(--color-accent-muted)]",
                  onClick && CLICKABLE_ROW_CLS,
                )}
                aria-current={isSelected ? "true" : undefined}
                {...(onClick ? clickableRowProps(onClick) : {})}
              >
                <td className="px-3 py-2.5 text-left font-mono text-xs font-medium text-[var(--color-text-primary)]">
                  <span>{p.repo1}</span>
                  <span className="px-1.5 font-sans font-normal text-[var(--color-text-tertiary)]">and</span>
                  <span>{p.repo2}</span>
                </td>
                <td
                  className={`px-3 py-2.5 text-right font-mono text-xs tabular-nums text-[var(--color-text-secondary)] ${HIDE_BELOW_MD}`}
                >
                  {p.filePairCount}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs tabular-nums text-[var(--color-text-primary)]">
                  {Math.round(p.maxStrength * 100)}%
                </td>
                <td
                  className={`whitespace-nowrap px-3 py-2.5 text-right font-mono text-xs tabular-nums text-[var(--color-text-tertiary)] ${HIDE_BELOW_LG}`}
                >
                  {/* formatDate, not toLocaleDateString(): the bare call resolves the
                      ambient locale, so Node and the browser can render the same date
                      differently and hydration fails. */}
                  <span title={p.lastDate ? formatDateTime(p.lastDate) : undefined}>
                    {p.lastDate ? formatDate(p.lastDate) : "unknown"}
                  </span>
                </td>
                {onSelectPair || onPrompt ? (
                  <td className="px-3 py-2 text-right">
                    <span className="inline-flex items-center justify-end gap-3">
                      {onSelectPair && (
                        <span
                          className={cn(
                            "whitespace-nowrap text-xs max-sm:sr-only",
                            isSelected
                              ? "font-medium text-[var(--color-accent-primary)]"
                              : "text-[var(--color-text-tertiary)]",
                          )}
                        >
                          {isSelected ? "Showing only these" : "Show only these"}
                        </span>
                      )}
                      {onPrompt && (
                        <AiPromptButton
                          variant="icon"
                          label={`AI prompt for ${p.repo1} and ${p.repo2}`}
                          onClick={() => onPrompt(p)}
                        />
                      )}
                    </span>
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
