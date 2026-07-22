"use client";

import * as React from "react";
import {
  DEAD_CODE_CONFIDENCE,
  deadCodeConfidenceTier,
} from "@repowise-dev/types/dead-code";
import { EmptyState } from "../shared/empty-state";
import { cn } from "../lib/cn";

export interface FindingsBreakdownItem {
  kind: string;
  confidence: number;
}

interface FindingsBreakdownGridProps {
  findings: FindingsBreakdownItem[];
  className?: string;
}

/** Rows of the matrix, on the shared boundaries rather than a local guess. */
const CONFIDENCE_TIERS = [
  { id: "high", label: `High (≥${DEAD_CODE_CONFIDENCE.HIGH})` },
  {
    id: "medium",
    label: `Medium (${DEAD_CODE_CONFIDENCE.MEDIUM}–${DEAD_CODE_CONFIDENCE.HIGH})`,
  },
  { id: "low", label: `Low (<${DEAD_CODE_CONFIDENCE.MEDIUM})` },
] as const;

const KIND_LABELS: Record<string, string> = {
  unreachable_file: "Unreachable file",
  unused_export: "Unused export",
  unused_internal: "Unused internal",
  zombie_package: "Zombie package",
};

/**
 * Confidence-tier × kind matrix for dead-code findings — surfaces where the
 * concentration is so reviewers know whether to start with high-confidence
 * file removals or low-confidence symbol pruning.
 */
export function FindingsBreakdownGrid({ findings, className }: FindingsBreakdownGridProps) {
  const { kinds, counts, max, tiers } = React.useMemo(() => {
    const kindSet = new Set<string>();
    const counts: Record<string, Record<string, number>> = {};
    let max = 0;
    for (const f of findings) {
      const tier = deadCodeConfidenceTier(f.confidence);
      const kind = f.kind ?? "unknown";
      kindSet.add(kind);
      counts[tier] ??= {};
      counts[tier][kind] = (counts[tier][kind] ?? 0) + 1;
      if (counts[tier][kind] > max) max = counts[tier][kind];
    }
    const ordered = Array.from(kindSet).sort();
    // The list endpoint floors at MEDIUM, so the low row is normally an empty
    // band across the whole matrix. Show it only when something is in it.
    const tiers = CONFIDENCE_TIERS.filter(
      (t) => t.id !== "low" || Object.keys(counts["low"] ?? {}).length > 0,
    );
    return { kinds: ordered, counts, max, tiers };
  }, [findings]);

  if (kinds.length === 0) {
    return (
      <EmptyState
        title="No findings yet"
        description="Nothing to break down until an analysis has produced dead-code findings."
        {...(className ? { className } : {})}
      />
    );
  }

  return (
    <div
      className={cn(
        "overflow-hidden rounded-md border border-[var(--color-border-default)]",
        className,
      )}
    >
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="bg-[var(--color-bg-elevated)]">
            <th className="px-3 py-2 text-left font-medium text-[var(--color-text-tertiary)]">
              Confidence
            </th>
            {kinds.map((k) => (
              <th
                key={k}
                className="px-2 py-2 text-right font-medium text-[var(--color-text-tertiary)]"
              >
                {KIND_LABELS[k] ?? k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tiers.map((tier) => (
            <tr key={tier.id} className="border-t border-[var(--color-border-default)]">
              <td className="px-3 py-2 text-[var(--color-text-secondary)]">{tier.label}</td>
              {kinds.map((k) => {
                const c = counts[tier.id]?.[k] ?? 0;
                const intensity = max > 0 ? c / max : 0;
                // Heat scales the fill alpha; the hue comes from the token for
                // each tier (error → warning → muted) via color-mix.
                const tierToken =
                  tier.id === "high"
                    ? "var(--color-error)"
                    : tier.id === "medium"
                      ? "var(--color-warning)"
                      : "var(--color-text-tertiary)";
                const alphaPct =
                  tier.id === "high"
                    ? Math.round((0.12 + intensity * 0.55) * 100)
                    : tier.id === "medium"
                      ? Math.round((0.1 + intensity * 0.5) * 100)
                      : Math.round((0.08 + intensity * 0.4) * 100);
                const bg =
                  c === 0
                    ? "transparent"
                    : `color-mix(in srgb, ${tierToken} ${alphaPct}%, transparent)`;
                return (
                  <td
                    key={k}
                    className="px-2 py-2 text-right tabular-nums"
                    style={{ backgroundColor: bg }}
                    title={`${c} ${KIND_LABELS[k] ?? k} at ${tier.label}`}
                  >
                    {c || ""}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
