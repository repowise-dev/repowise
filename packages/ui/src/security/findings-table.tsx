"use client";

import * as React from "react";
import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Input } from "../ui/input";
import { EmptyState } from "../shared/empty-state";
import { CiHint } from "../shared/ci-hint";
import { Segmented } from "../shared/segmented";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { AiPromptButton } from "../health/ai-prompt-button";
import { SeverityMark } from "../health/severity-mark";
import type { Severity } from "../health/tokens";
import { formatDate, formatDateTime, formatRelativeTimeOrNull } from "../lib/format";
import { securityLevel, securityPathClass, type SecurityLevel } from "./posture";
import type { SecurityFinding } from "@repowise-dev/types";

// One declaration of the wire shape, not a third hand-kept copy: this file's
// local interface had already drifted behind the endpoint, which is how the
// line number went unrendered.
export type { SecurityFinding };

// Break only at path separators; `anywhere` is the last resort for a single
// segment wider than the box.
function BreakablePath({ path }: { path: string }) {
  const parts = path.split("/");
  return (
    <>
      {parts.map((part, i) => (
        <React.Fragment key={i}>
          {part}
          {i < parts.length - 1 && (
            <>
              /<wbr />
            </>
          )}
        </React.Fragment>
      ))}
    </>
  );
}

// The scanner's `med` is the shared scale's `medium`. No level maps to
// critical: a pattern match is a lead to confirm, not a proven path to harm.
const MARK: Record<SecurityLevel, Severity> = { high: "high", med: "medium", low: "low" };
const LEVEL_RANK: Record<SecurityLevel, number> = { high: 0, med: 1, low: 2 };
const CLASS_RANK = { source: 0, test: 1, docs: 2 } as const;

type SeverityFilter = "all" | SecurityLevel;
const FILTERS: { value: SeverityFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "high", label: "High" },
  { value: "med", label: "Medium" },
  { value: "low", label: "Low" },
];

/**
 * `path:line` for a finding, degrading honestly.
 *
 * The server checks the stored line against the live tree, so there are three
 * states and they must look different: a confirmed line, a line it could not
 * confirm (prefixed `~`, never presented as fact), and no line at all when the
 * flagged code has moved away entirely. A wrong line here sends the reader to
 * innocent code looking authoritative, which is worse than showing none.
 *
 * The path is the row's name, so it wraps rather than truncates.
 */
function FindingLocation({ finding }: { finding: SecurityFinding }) {
  const line = finding.line_number;
  const verified = finding.line_verified;
  // Tests and docs are labelled, never headlined: shipped code ranks first.
  const where = securityPathClass(finding.file_path);
  const tag =
    where === "source" ? null : (
      <span className="ml-1.5 font-sans text-2xs text-[var(--color-text-tertiary)]">{where}</span>
    );

  // Every state carries words, not just a colour and a tooltip: `title` is
  // invisible to touch and to assistive tech, which is why VerificationBadge
  // keeps an sr-only label beside its icon. Same convention here.
  if (line == null) {
    return (
      <span
        className="block min-w-[14rem] [overflow-wrap:anywhere] font-mono text-xs text-[var(--color-text-primary)]"
        title={`${finding.file_path}: the flagged code is no longer at the recorded line`}
      >
        <BreakablePath path={finding.file_path} />
        <span className="ml-1.5 not-italic text-2xs text-[var(--color-text-tertiary)]">
          (line moved)
        </span>
        {tag}
      </span>
    );
  }

  return (
    <span
      className="block min-w-[14rem] [overflow-wrap:anywhere] font-mono text-xs text-[var(--color-text-primary)]"
      title={
        verified
          ? `${finding.file_path}:${line}`
          : `${finding.file_path}:${line}: could not be confirmed against the current file`
      }
    >
      <BreakablePath path={finding.file_path} />
      <wbr />
      <span
        className={
          verified ? "text-[var(--color-text-secondary)]" : "text-[var(--color-text-tertiary)]"
        }
      >
        :{verified ? "" : "~"}
        {line}
      </span>
      {!verified && <span className="sr-only"> (line unconfirmed)</span>}
      {tag}
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
  const [sev, setSev] = useState<SeverityFilter>("all");

  // Options and counts come from the unfiltered list, so picking one level
  // never hides the others.
  const options = useMemo(() => {
    const c: Record<SeverityFilter, number> = { all: findings.length, high: 0, med: 0, low: 0 };
    for (const f of findings) c[securityLevel(f.severity)] += 1;
    return FILTERS.map((o) => ({ ...o, count: c[o.value].toLocaleString() }));
  }, [findings]);

  const filtered = useMemo(() => {
    let items = findings;
    if (sev !== "all") items = items.filter((f) => securityLevel(f.severity) === sev);
    if (q) {
      const needle = q.toLowerCase();
      items = items.filter(
        (f) =>
          f.file_path.toLowerCase().includes(needle) ||
          f.kind.toLowerCase().includes(needle) ||
          (f.snippet ?? "").toLowerCase().includes(needle),
      );
    }
    // What to do first: shipped source before tests before docs, then severity.
    return [...items].sort(
      (a, b) =>
        CLASS_RANK[securityPathClass(a.file_path)] - CLASS_RANK[securityPathClass(b.file_path)] ||
        LEVEL_RANK[securityLevel(a.severity)] - LEVEL_RANK[securityLevel(b.severity)],
    );
  }, [findings, q, sev]);

  const columns = useMemo(() => {
    const cols: ResponsiveColumn<SecurityFinding>[] = [
      {
        key: "severity",
        header: "Severity",
        headerClassName: "w-24",
        render: (f) => <SeverityMark severity={MARK[securityLevel(f.severity)]} />,
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
            {f.snippet || "—"}
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
      <div className="flex flex-col items-center gap-2">
        <EmptyState
          title="No findings"
          description="The pattern scan has no security findings to show for this repository."
        />
        <CiHint command="repowise security check" checks="what each change adds" />
      </div>
    );
  }

  const clear = () => {
    setQ("");
    setSev("all");
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search
            aria-hidden
            className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]"
          />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search file, kind, or snippet…"
            aria-label="Search findings"
            className="h-8 w-full pl-8 text-xs"
          />
        </div>
        <Segmented label="Severity" value={sev} onChange={setSev} options={options} />
      </div>

      {/* Scrolls sideways inside its own frame on a phone rather than
          stacking: the path column keeps a readable minimum width. Windowed,
          because the list is open by default and can hold 500 rows. */}
      <ResponsiveTable
        columns={columns}
        rows={filtered}
        rowKey={(f) => String(f.id)}
        caption="Security findings"
        {...(onSelect ? { onRowClick: onSelect } : {})}
        virtualize={{}}
        empty={
          <EmptyState
            tone="filtered"
            title="No findings match these filters"
            action={{ label: "Clear filters", onClick: clear }}
          />
        }
      />
    </div>
  );
}
