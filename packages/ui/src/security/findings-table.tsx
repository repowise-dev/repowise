"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Badge } from "../ui/badge";
import { Input } from "../ui/input";
import { EmptyState } from "../shared/empty-state";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { AiPromptButton } from "../health/ai-prompt-button";
import { formatDate, formatDateTime, formatRelativeTimeOrNull } from "../lib/format";
import type { SecurityFinding } from "@repowise-dev/types";

// One declaration of the wire shape, not a third hand-kept copy: this file's
// local interface had already drifted behind the endpoint, which is how the
// line number went unrendered.
export type { SecurityFinding };

const SEVERITY_VARIANT: Record<string, "outdated" | "stale" | "outline"> = {
  high: "outdated",
  med: "stale",
  low: "outline",
};

/**
 * `path:line` for a finding, degrading honestly.
 *
 * The server checks the stored line against the live tree, so there are three
 * states and they must look different: a confirmed line, a line it could not
 * confirm (prefixed `~`, never presented as fact), and no line at all when the
 * flagged code has moved away entirely. A wrong line here sends the reader to
 * innocent code looking authoritative, which is worse than showing none.
 */
function FindingLocation({ finding }: { finding: SecurityFinding }) {
  const line = finding.line_number;
  const verified = finding.line_verified;

  // Every state carries words, not just a colour and a tooltip: `title` is
  // invisible to touch and to assistive tech, which is why VerificationBadge
  // keeps an sr-only label beside its icon. Same convention here.
  if (line == null) {
    return (
      <span
        className="block max-w-[280px] truncate font-mono text-xs text-[var(--color-text-primary)]"
        title={`${finding.file_path} — the flagged code is no longer at the recorded line`}
      >
        {finding.file_path}
        <span className="ml-1.5 not-italic text-2xs text-[var(--color-text-tertiary)]">
          (line moved)
        </span>
      </span>
    );
  }

  return (
    <span
      className="block max-w-[280px] truncate font-mono text-xs text-[var(--color-text-primary)]"
      title={
        verified
          ? `${finding.file_path}:${line}`
          : `${finding.file_path}:${line} — could not be confirmed against the current file`
      }
    >
      {finding.file_path}
      <span
        className={
          verified ? "text-[var(--color-text-secondary)]" : "text-[var(--color-text-tertiary)]"
        }
      >
        :{verified ? "" : "~"}
        {line}
      </span>
      {!verified && <span className="sr-only"> (line unconfirmed)</span>}
    </span>
  );
}

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
        render: (f) => <FindingLocation finding={f} />,
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
        key: "commit_at",
        header: "Committed",
        headerClassName: "w-28",
        priority: 3,
        // Only history findings carry an introducing commit; a working-tree
        // finding has no commit to date, hence the dash.
        render: (f) => (
          <span
            className="text-xs tabular-nums text-[var(--color-text-tertiary)]"
            title={f.commit_at ? formatDateTime(f.commit_at) : undefined}
          >
            {formatRelativeTimeOrNull(f.commit_at)}
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
            {formatDate(f.detected_at)}
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
