"use client";

import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import type { DocPage } from "@repowise-dev/types/docs";

const CONFIDENCE_BUCKETS = [
  { key: "high", label: "High (≥0.8)", min: 0.8 },
  { key: "med", label: "Medium (0.5–0.8)", min: 0.5 },
  { key: "low", label: "Low (<0.5)", min: 0 },
] as const;

const FRESHNESS = ["fresh", "stale", "outdated"] as const;

const CELL_COLORS: Record<string, string> = {
  "high::fresh": "rgba(16, 185, 129, 0.20)",
  "high::stale": "rgba(245, 158, 11, 0.18)",
  "high::outdated": "rgba(239, 68, 68, 0.18)",
  "med::fresh": "rgba(16, 185, 129, 0.10)",
  "med::stale": "rgba(245, 158, 11, 0.10)",
  "med::outdated": "rgba(239, 68, 68, 0.10)",
  "low::fresh": "rgba(16, 185, 129, 0.05)",
  "low::stale": "rgba(245, 158, 11, 0.05)",
  "low::outdated": "rgba(239, 68, 68, 0.05)",
};

export interface ConfidenceVsFreshnessMatrixProps {
  pages: DocPage[];
}

export function ConfidenceVsFreshnessMatrix({ pages }: ConfidenceVsFreshnessMatrixProps) {
  const grid = new Map<string, number>();
  for (const p of pages) {
    const bucket =
      p.confidence >= 0.8 ? "high" : p.confidence >= 0.5 ? "med" : "low";
    const key = `${bucket}::${p.freshness_status}`;
    grid.set(key, (grid.get(key) ?? 0) + 1);
  }

  if (pages.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Confidence × freshness</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="overflow-x-auto">
          <table className="text-xs w-full">
            <thead>
              <tr>
                <th className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)] py-1.5 font-medium pr-3">
                  Confidence \ Freshness
                </th>
                {FRESHNESS.map((f) => (
                  <th
                    key={f}
                    className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)] py-1.5 font-medium px-3 capitalize"
                  >
                    {f}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {CONFIDENCE_BUCKETS.map((b) => (
                <tr key={b.key}>
                  <td className="py-1.5 pr-3 text-[var(--color-text-secondary)]">{b.label}</td>
                  {FRESHNESS.map((f) => {
                    const key = `${b.key}::${f}`;
                    const n = grid.get(key) ?? 0;
                    return (
                      <td
                        key={f}
                        className="rounded border border-[var(--color-border-default)] py-2 px-3 align-top tabular-nums font-medium text-[var(--color-text-primary)]"
                        style={{ backgroundColor: CELL_COLORS[key] ?? "transparent" }}
                      >
                        {n}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
