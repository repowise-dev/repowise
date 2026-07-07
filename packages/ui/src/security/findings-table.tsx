"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Badge } from "../ui/badge";
import { Input } from "../ui/input";
import { EmptyState } from "../shared/empty-state";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { AiPromptButton } from "../health/ai-prompt-button";

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
  /** When set, each row shows an "AI fix prompt" action that calls this. */
  onGeneratePrompt?: (finding: SecurityFinding) => void;
}

export function SecurityFindingsTable({ findings, onSelect, onGeneratePrompt }: SecurityFindingsTableProps) {
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

  const columns = useMemo(() => {
    const cols: ResponsiveColumn<SecurityFinding>[] = [
      {
        key: "severity",
        header: "Severity",
        headerClassName: "w-20",
        render: (f) => (
          <Badge variant={SEVERITY_VARIANT[f.severity] ?? "outline"} className="capitalize">
            {f.severity}
          </Badge>
        ),
      },
      {
        key: "file_path",
        header: "File",
        render: (f) => (
          <span
            className="block max-w-[280px] truncate font-mono text-xs text-[var(--color-text-primary)]"
            title={f.file_path}
          >
            {f.file_path}
          </span>
        ),
      },
      {
        key: "kind",
        header: "Kind",
        headerClassName: "w-40",
        render: (f) => <span className="text-xs text-[var(--color-text-secondary)]">{f.kind}</span>,
      },
      {
        key: "snippet",
        header: "Snippet",
        priority: 2,
        render: (f) => (
          <span
            className="block max-w-[320px] truncate font-mono text-xs text-[var(--color-text-tertiary)]"
            title={f.snippet ?? ""}
          >
            {f.snippet ?? "—"}
          </span>
        ),
      },
      {
        key: "detected_at",
        header: "Detected",
        headerClassName: "w-28",
        priority: 3,
        render: (f) => (
          <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
            {new Date(f.detected_at).toLocaleDateString()}
          </span>
        ),
      },
    ];
    if (onGeneratePrompt) {
      cols.push({
        key: "actions",
        header: "",
        headerClassName: "w-10",
        hideInCard: true,
        render: (f) => (
          <span onClick={(e) => e.stopPropagation()}>
            <AiPromptButton
              variant="icon"
              label="AI fix prompt"
              onClick={() => onGeneratePrompt(f)}
            />
          </span>
        ),
      });
    }
    return cols;
  }, [onGeneratePrompt]);

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

      <ResponsiveTable
        columns={columns}
        rows={filtered}
        rowKey={(f) => String(f.id)}
        caption="Security findings"
        {...(onSelect ? { onRowClick: onSelect } : {})}
        stacked="sm"
        empty={
          <p className="text-sm text-[var(--color-text-tertiary)] py-6 text-center">No matches.</p>
        }
      />
    </div>
  );
}
