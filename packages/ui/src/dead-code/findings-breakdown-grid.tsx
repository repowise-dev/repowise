"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface FindingsBreakdownItem {
  kind: string;
  confidence: number;
}

interface FindingsBreakdownGridProps {
  findings: FindingsBreakdownItem[];
  className?: string;
}

const CONFIDENCE_TIERS = [
  { id: "high", label: "High (≥0.8)", min: 0.8 },
  { id: "med", label: "Medium (0.5–0.8)", min: 0.5 },
  { id: "low", label: "Low (<0.5)", min: 0 },
] as const;

const KIND_LABELS: Record<string, string> = {
  unreachable_file: "Unreachable file",
  unused_export: "Unused export",
  unused_internal: "Unused internal",
  zombie_package: "Zombie package",
};

function tierFor(confidence: number) {
  for (const t of CONFIDENCE_TIERS) if (confidence >= t.min) return t.id;
  return "low";
}

/**
 * Confidence-tier × kind matrix for dead-code findings — surfaces where the
 * concentration is so reviewers know whether to start with high-confidence
 * file removals or low-confidence symbol pruning.
 */
export function FindingsBreakdownGrid({ findings, className }: FindingsBreakdownGridProps) {
  const { kinds, counts, max } = React.useMemo(() => {
    const kindSet = new Set<string>();
    const counts: Record<string, Record<string, number>> = {};
    let max = 0;
    for (const f of findings) {
      const tier = tierFor(f.confidence);
      const kind = f.kind ?? "unknown";
      kindSet.add(kind);
      counts[tier] ??= {};
      counts[tier][kind] = (counts[tier][kind] ?? 0) + 1;
      if (counts[tier][kind] > max) max = counts[tier][kind];
    }
    const ordered = Array.from(kindSet).sort();
    return { kinds: ordered, counts, max };
  }, [findings]);

  if (kinds.length === 0) {
    return (
      <div
        className={cn(
          "rounded-md border border-dashed border-[var(--color-border-default)] p-4 text-center text-xs text-[var(--color-text-tertiary)]",
          className,
        )}
      >
        No findings yet.
      </div>
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
          {CONFIDENCE_TIERS.map((tier) => (
            <tr key={tier.id} className="border-t border-[var(--color-border-default)]">
              <td className="px-3 py-2 text-[var(--color-text-secondary)]">{tier.label}</td>
              {kinds.map((k) => {
                const c = counts[tier.id]?.[k] ?? 0;
                const intensity = max > 0 ? c / max : 0;
                const bg =
                  c === 0
                    ? "transparent"
                    : tier.id === "high"
                      ? `rgba(244,63,94,${0.12 + intensity * 0.55})`
                      : tier.id === "med"
                        ? `rgba(245,158,11,${0.10 + intensity * 0.5})`
                        : `rgba(148,163,184,${0.08 + intensity * 0.4})`;
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
