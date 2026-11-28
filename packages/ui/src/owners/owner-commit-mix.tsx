import * as React from "react";
import { ProportionBar } from "../shared/proportion-bar";
import { EmptyState } from "../shared/empty-state";
import { commitCategoryColor } from "../shared/series";
import type { OwnershipLabels } from "./labels";
import { formatNumber } from "../lib/format";

const pct = (part: number, whole: number): number =>
  whole > 0 ? Math.round((part / whole) * 100) : 0;

/**
 * Commit mix, as one share bar. Each category keeps its fixed colour
 * (commitCategoryColor), the same as on every other chart of commit types.
 * Zero-count categories are dropped: the endpoint returns the full key set,
 * and a labelled bar with nothing in it reports a measurement of nothing.
 */
export function OwnerCommitMix({
  categories,
  labels,
}: {
  categories: Record<string, number>;
  labels: OwnershipLabels;
}) {
  const entries = Object.entries(categories ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);

  if (entries.length === 0) {
    return (
      <EmptyState
        className="p-6"
        title={labels.commitMixEmptyTitle}
        description={labels.commitMixEmptyDescription}
      />
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs leading-relaxed text-[var(--color-text-tertiary)]">
        {labels.commitMixDescription(total)}
      </p>
      <ProportionBar
        className="max-w-[420px]"
        label={labels.commitMixBarLabel}
        segments={entries.map(([category, n]) => ({
          key: category,
          label: category.charAt(0).toUpperCase() + category.slice(1),
          value: n,
          detail: `${formatNumber(n)} · ${pct(n, total)}%`,
          color: commitCategoryColor(category),
        }))}
      />
    </div>
  );
}
