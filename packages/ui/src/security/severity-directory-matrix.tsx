"use client";

import * as React from "react";
import { cn } from "../lib/cn";
import type { SecurityFinding } from "./findings-table";

export interface SeverityDirectoryMatrixProps {
  findings: SecurityFinding[];
  /** Path-segment depth to group at (default 2). */
  depth?: number;
  /** Cap the number of directory rows (highest total first). */
  maxRows?: number;
  className?: string;
}

const SEVERITY_ORDER = ["high", "med", "low"] as const;
const SEVERITY_LABEL: Record<string, string> = {
  high: "High",
  med: "Medium",
  low: "Low",
};
const SEVERITY_TOKEN: Record<string, string> = {
  high: "var(--color-error)",
  med: "var(--color-warning)",
  low: "var(--color-text-tertiary)",
};

/**
 * Severity × directory heat matrix — where the security findings concentrate,
 * so reviewers know which area to triage first. Mirrors the dead-code
 * confidence × kind grid idiom; intensity rides on a token via color-mix.
 */
export function SeverityDirectoryMatrix({
  findings,
  depth = 2,
  maxRows = 14,
  className,
}: SeverityDirectoryMatrixProps) {
  const { rows, max } = React.useMemo(() => {
    const byDir = new Map<string, { dir: string; counts: Record<string, number>; total: number }>();
    let max = 0;
    for (const f of findings) {
      const segments = f.file_path.split("/").slice(0, depth);
      const dir = segments.length === 0 ? "(root)" : segments.join("/");
      const sev = SEVERITY_ORDER.includes(f.severity as (typeof SEVERITY_ORDER)[number])
        ? f.severity
        : "low";
      const cur = byDir.get(dir) ?? { dir, counts: {}, total: 0 };
      cur.counts[sev] = (cur.counts[sev] ?? 0) + 1;
      cur.total += 1;
      if (cur.counts[sev] > max) max = cur.counts[sev];
      byDir.set(dir, cur);
    }
    const ordered = Array.from(byDir.values())
      .sort((a, b) => b.total - a.total)
      .slice(0, maxRows);
    return { rows: ordered, max };
  }, [findings, depth, maxRows]);

  if (rows.length === 0) {
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
              Directory
            </th>
            {SEVERITY_ORDER.map((s) => (
              <th
                key={s}
                className="px-2 py-2 text-right font-medium text-[var(--color-text-tertiary)]"
              >
                {SEVERITY_LABEL[s]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.dir} className="border-t border-[var(--color-border-default)]">
              <td
                className="max-w-[260px] truncate px-3 py-2 font-mono text-[var(--color-text-secondary)]"
                title={r.dir}
              >
                {r.dir}
              </td>
              {SEVERITY_ORDER.map((s) => {
                const c = r.counts[s] ?? 0;
                const intensity = max > 0 ? c / max : 0;
                const alphaPct = Math.round((0.1 + intensity * 0.55) * 100);
                const bg =
                  c === 0
                    ? "transparent"
                    : `color-mix(in srgb, ${SEVERITY_TOKEN[s]} ${alphaPct}%, transparent)`;
                return (
                  <td
                    key={s}
                    className="px-2 py-2 text-right tabular-nums"
                    style={{ backgroundColor: bg }}
                    title={`${c} ${SEVERITY_LABEL[s]} in ${r.dir}`}
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
