"use client";

import * as React from "react";
import { cn } from "../lib/cn";
import type { SecurityFinding } from "./findings-table";
import { securityLevel, type SecurityLevel } from "./posture";

export interface SeverityDirectoryMatrixProps {
  findings: SecurityFinding[];
  /** Path-segment depth to group at (default 2). */
  depth?: number;
  /** Cap the number of directory rows (highest total first). */
  maxRows?: number;
  className?: string;
}

const SEVERITY_ORDER: SecurityLevel[] = ["high", "med", "low"];
const SEVERITY_LABEL: Record<SecurityLevel, string> = {
  high: "High",
  med: "Medium",
  low: "Low",
};

/**
 * Where the security findings concentrate: a plain count table of directory
 * by severity, highest total first. Counts only, no cell tints: a heat ground
 * made a directory of fixtures look like the page's emergency.
 */
export function SeverityDirectoryMatrix({
  findings,
  depth = 2,
  maxRows = 14,
  className,
}: SeverityDirectoryMatrixProps) {
  const rows = React.useMemo(() => {
    const byDir = new Map<string, { dir: string; counts: Record<SecurityLevel, number>; total: number }>();
    for (const f of findings) {
      const segments = f.file_path.split("/").slice(0, depth);
      const dir = segments.length === 0 ? "(root)" : segments.join("/");
      const cur = byDir.get(dir) ?? { dir, counts: { high: 0, med: 0, low: 0 }, total: 0 };
      cur.counts[securityLevel(f.severity)] += 1;
      cur.total += 1;
      byDir.set(dir, cur);
    }
    return Array.from(byDir.values())
      .sort((a, b) => b.total - a.total)
      .slice(0, maxRows);
  }, [findings, depth, maxRows]);

  if (rows.length === 0) return null;

  return (
    <div
      className={cn(
        "overflow-x-auto rounded-md border border-[var(--color-border-default)]",
        className,
      )}
    >
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="text-2xs uppercase tracking-wider text-[var(--color-text-tertiary)]">
            <th scope="col" className="px-3 py-2 text-left font-medium">
              Directory
            </th>
            {SEVERITY_ORDER.map((s) => (
              <th key={s} scope="col" className="px-3 py-2 text-right font-medium">
                {SEVERITY_LABEL[s]}
              </th>
            ))}
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.dir} className="border-t border-[var(--color-table-divider)]">
              <th
                scope="row"
                className="min-w-[10rem] break-all px-3 py-2 text-left font-mono font-normal text-[var(--color-text-secondary)]"
              >
                {r.dir}
              </th>
              {SEVERITY_ORDER.map((s) => {
                const c = r.counts[s];
                return (
                  <td
                    key={s}
                    className={cn(
                      "px-3 py-2 text-right font-mono tabular-nums",
                      c ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-tertiary)]",
                    )}
                  >
                    {c}
                  </td>
                );
              })}
              <td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--color-text-secondary)]">
                {r.total}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
