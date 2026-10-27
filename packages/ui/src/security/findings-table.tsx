"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Input } from "../ui/input";
import { EmptyState } from "../shared/empty-state";
import { CiHint } from "../shared/ci-hint";
import { Segmented } from "../shared/segmented";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { PromptForRowsButton, RowPromptAction } from "../health/row-prompt-action";
import { AGENT_PROMPT_LABEL } from "../health/ai-prompt-action";
import { SeverityMark } from "../health/severity-mark";
import type { Severity } from "../health/tokens";
import { formatDate, formatDateTime, formatRelativeTimeOrNull } from "../lib/format";
import { FilePath } from "../shared/file-path";
import { securityLevel, type SecurityLevel } from "./posture";
import type { SecurityFinding } from "@repowise-dev/types";

// One declaration of the wire shape, not a third hand-kept copy: this file's
// local interface had already drifted behind the endpoint, which is how the
// line number went unrendered.
export type { SecurityFinding };

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
 * The path is the row's name: its file name never clips.
 */
function FindingLocation({ finding }: { finding: SecurityFinding }) {
  const line = finding.line_number;
  const verified = finding.line_verified;
  // Tests and docs are labelled, never headlined: shipped code ranks first.
  const where = finding.path_class ?? "source";
  const tag =
    where === "source" ? null : (
      <span className="ml-1.5 font-sans text-2xs text-[var(--color-text-tertiary)]">{where}</span>
    );

  // Every state carries words, not just a colour: a line the server could
  // not confirm is marked `~` and named, and a line that moved away is named.
  // The file name leads; the directory clips before it, and the tooltip and
  // accessible name keep the whole `path:line`.
  const note = line == null ? "line moved" : verified ? null : "unconfirmed";
  return (
    <span className="flex min-w-[14rem] max-w-[28rem] items-baseline text-[var(--color-text-primary)]">
      <FilePath
        path={finding.file_path}
        line={line == null ? null : verified ? line : `~${line}`}
        fold
        className="min-w-0"
      />
      {note ? (
        <span className="ml-1.5 shrink-0 font-sans text-2xs text-[var(--color-text-tertiary)]">
          ({note})
        </span>
      ) : null}
      {tag ? <span className="shrink-0">{tag}</span> : null}
    </span>
  );
}

export interface SecurityFindingsTableProps {
  findings: SecurityFinding[];
  onSelect?: (finding: SecurityFinding) => void;
  /**
   * When set, each row leads with an agent-prompt action that calls this:
   * the row's primary verb, since fixes happen through the reader's agent.
   */
  onGeneratePrompt?: (finding: SecurityFinding) => void;
  /** The prompt action's label. */
  promptLabel?: string;
  /** One prompt over every finding the current filters show. */
  onGenerateBatchPrompt?: (findings: SecurityFinding[]) => void;
  /** The batch action's label for `n` findings. */
  batchPromptLabel?: (count: number) => string;
}

export function SecurityFindingsTable({
  findings,
  onSelect,
  onGeneratePrompt,
  promptLabel = AGENT_PROMPT_LABEL,
  onGenerateBatchPrompt,
  batchPromptLabel,
}: SecurityFindingsTableProps) {
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
        CLASS_RANK[a.path_class ?? "source"] - CLASS_RANK[b.path_class ?? "source"] ||
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
            className="block max-w-[240px] truncate font-mono text-xs text-[var(--color-text-tertiary)]"
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
        render: (f) => (
          <RowPromptAction label={promptLabel} subject={f.file_path} onClick={() => onGeneratePrompt(f)} />
        ),
      });
    }
    return cols;
  }, [onGeneratePrompt, promptLabel]);

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
        {onGenerateBatchPrompt && (
          <PromptForRowsButton
            count={filtered.length}
            onClick={() => onGenerateBatchPrompt(filtered)}
            {...(batchPromptLabel ? { label: batchPromptLabel } : {})}
            className="sm:ml-auto"
          />
        )}
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
