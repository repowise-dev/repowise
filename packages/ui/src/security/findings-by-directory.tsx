"use client";

import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import type { SecurityFinding } from "./findings-table";

export interface FindingsByDirectoryProps {
  findings: SecurityFinding[];
  /** Path-segment depth to group at (default 2). */
  depth?: number;
}

export function FindingsByDirectory({ findings, depth = 2 }: FindingsByDirectoryProps) {
  const grouped = new Map<string, { dir: string; high: number; med: number; low: number; total: number }>();
  for (const f of findings) {
    const segments = f.file_path.split("/").slice(0, depth);
    const dir = segments.length === 0 ? "(root)" : segments.join("/");
    const cur = grouped.get(dir) ?? { dir, high: 0, med: 0, low: 0, total: 0 };
    if (f.severity === "high") cur.high += 1;
    else if (f.severity === "med") cur.med += 1;
    else cur.low += 1;
    cur.total += 1;
    grouped.set(dir, cur);
  }

  const rows = Array.from(grouped.values()).sort((a, b) => b.total - a.total).slice(0, 16);
  const max = rows.reduce((m, r) => Math.max(m, r.total), 1);

  if (rows.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Findings by directory</CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-2">
        {rows.map((r) => (
          <div key={r.dir} className="text-xs">
            <div className="flex items-center justify-between mb-1">
              <span className="font-mono text-[var(--color-text-primary)] truncate">{r.dir}</span>
              <span className="tabular-nums text-[var(--color-text-tertiary)] ml-2">{r.total}</span>
            </div>
            <div className="flex h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
              {r.high > 0 && (
                <div style={{ width: `${(r.high / max) * 100}%`, backgroundColor: "#ef4444" }} />
              )}
              {r.med > 0 && (
                <div style={{ width: `${(r.med / max) * 100}%`, backgroundColor: "#f59e0b" }} />
              )}
              {r.low > 0 && (
                <div style={{ width: `${(r.low / max) * 100}%`, backgroundColor: "#10b981" }} />
              )}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
