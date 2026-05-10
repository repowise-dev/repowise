"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Badge } from "../ui/badge";
import { Input } from "../ui/input";
import { EmptyState } from "../shared/empty-state";

export interface SecurityFinding {
  id: number;
  file_path: string;
  kind: string;
  severity: string;
  snippet: string | null;
  detected_at: string;
}

const SEVERITY_VARIANT: Record<string, "outdated" | "stale" | "outline"> = {
  high: "outdated",
  med: "stale",
  low: "outline",
};

export interface SecurityFindingsTableProps {
  findings: SecurityFinding[];
  onSelect?: (finding: SecurityFinding) => void;
}

export function SecurityFindingsTable({ findings, onSelect }: SecurityFindingsTableProps) {
  const [q, setQ] = useState("");
  const [sev, setSev] = useState<"all" | "high" | "med" | "low">("all");

  const filtered = useMemo(() => {
    let items = findings;
    if (sev !== "all") items = items.filter((f) => f.severity === sev);
    if (q) {
      const needle = q.toLowerCase();
      items = items.filter(
        (f) =>
          f.file_path.toLowerCase().includes(needle) ||
          f.kind.toLowerCase().includes(needle) ||
          (f.snippet ?? "").toLowerCase().includes(needle),
      );
    }
    return items;
  }, [findings, q, sev]);

  if (findings.length === 0) {
    return (
      <EmptyState
        title="No findings"
        description="No security findings detected on this repo. Re-run analysis to refresh."
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search file, kind, or snippet…"
            className="pl-8 h-8 w-full sm:w-72 text-xs"
          />
        </div>
        <div className="flex rounded-md border border-[var(--color-border-default)] overflow-hidden text-xs">
          {(["all", "high", "med", "low"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSev(s)}
              className={
                sev === s
                  ? "px-2.5 py-1.5 bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
                  : "px-2.5 py-1.5 bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
              }
            >
              {s}
              {s !== "all" && (
                <span className="ml-1 text-[10px] opacity-70">
                  ({findings.filter((f) => f.severity === s).length})
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-[var(--color-border-default)] overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="sticky top-0 z-10 bg-[var(--color-bg-elevated)]">
            <tr className="border-b border-[var(--color-border-default)]">
              <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-20">
                Severity
              </th>
              <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                File
              </th>
              <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-40">
                Kind
              </th>
              <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                Snippet
              </th>
              <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-28">
                Detected
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((f) => (
              <tr
                key={f.id}
                onClick={onSelect ? () => onSelect(f) : undefined}
                className={
                  "border-b border-[var(--color-border-default)] last:border-0 hover:bg-[var(--color-bg-elevated)] transition-colors" +
                  (onSelect ? " cursor-pointer" : "")
                }
              >
                <td className="px-3 py-2">
                  <Badge variant={SEVERITY_VARIANT[f.severity] ?? "outline"} className="capitalize">
                    {f.severity}
                  </Badge>
                </td>
                <td className="px-3 py-2 font-mono text-xs text-[var(--color-text-primary)] max-w-[280px]">
                  <span className="block truncate" title={f.file_path}>{f.file_path}</span>
                </td>
                <td className="px-3 py-2 text-xs text-[var(--color-text-secondary)]">{f.kind}</td>
                <td className="px-3 py-2 font-mono text-[11px] text-[var(--color-text-tertiary)] max-w-[320px]">
                  <span className="block truncate" title={f.snippet ?? ""}>
                    {f.snippet ?? "—"}
                  </span>
                </td>
                <td className="px-3 py-2 text-[11px] text-[var(--color-text-tertiary)] tabular-nums">
                  {new Date(f.detected_at).toLocaleDateString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {filtered.length === 0 && (
        <p className="text-sm text-[var(--color-text-tertiary)] py-6 text-center">No matches.</p>
      )}
    </div>
  );
}
