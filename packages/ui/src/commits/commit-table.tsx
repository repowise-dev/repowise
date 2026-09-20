"use client";

import { useMemo, useState } from "react";
import { Bug, Search } from "lucide-react";
import { AgentBadge, NewContributorBadge, isNewContributor } from "./agent-badge";
import { PriorityBadge } from "./priority-badge";
import { Input } from "../ui/input";
import { EmptyState } from "../shared/empty-state";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { ResultsFooter } from "../shared/results-footer";
import { formatLOC, formatRelativeTime } from "../lib/format";
import { cn } from "../lib/cn";
import type { Commit, ReviewPriority } from "@repowise-dev/types/git";

export type CommitSort = "risk" | "date";
export type CommitAuthorship = "all" | "human" | "agent";
export type CommitKind = "all" | "high" | "fixes";

export interface CommitTableProps {
  commits: Commit[];
  /** Server-driven ordering. `risk` = review-priority order, `date` = recency. */
  sort: CommitSort;
  onSortChange?: (sort: CommitSort) => void;
  /** Server-driven authorship filter (agent provenance). Control omitted when
   *  no handler is provided. */
  authorship?: CommitAuthorship;
  onAuthorshipChange?: (authorship: CommitAuthorship) => void;
  /** Server-driven review-priority / fixes filter. */
  kind?: CommitKind;
  onKindChange?: (kind: CommitKind) => void;
  /** Repo-wide counts for the filter chips. Page-scoped counts read as
   *  "All (50), High priority (50)" on a risk-sorted feed, where every loaded
   *  row is already in the top tercile. */
  counts?: { all: number; high: number; fixes: number } | undefined;
  onSelect?: (commit: Commit) => void;
  total?: number;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
}

type CommitRow = Commit & { _idx: number };

const COLUMNS: ResponsiveColumn<CommitRow>[] = [
  {
    key: "rank",
    header: "#",
    headerClassName: "w-8",
    hideInCard: true,
    render: (c) => (
      <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">{c._idx + 1}</span>
    ),
  },
  {
    key: "commit",
    header: "Commit",
    cellClassName: "min-w-[200px] max-w-[460px]",
    render: (c) => (
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-[var(--color-text-tertiary)] shrink-0">
          {c.short_sha}
        </span>
        {c.is_fix && <Bug className="h-3 w-3 shrink-0 text-[var(--color-error)]" />}
        {/* Wraps rather than truncates. This is the primary column, and an
            ellipsis here reports a layout decision to the reader as a shorter
            commit message: "release: v0.4.0, language-support upgrades across
            all Goo…" is the page hiding the part that says which. */}
        <span className="min-w-0 text-xs text-[var(--color-text-primary)] [text-wrap:pretty]">
          {c.subject || "(no subject)"}
        </span>
      </div>
    ),
  },
  {
    key: "author",
    header: "Author",
    priority: 2,
    cellClassName: "max-w-[220px]",
    render: (c) => (
      <div className="flex items-center gap-1.5 min-w-0 text-xs text-[var(--color-text-secondary)]">
        <span className="truncate">{c.author_name || "—"}</span>
        {c.agent_name && <AgentBadge agentName={c.agent_name} tier={c.agent_autonomy_tier} />}
        {!c.agent_name && isNewContributor(c.author_commit_count) && (
          <NewContributorBadge commitCount={c.author_commit_count as number} />
        )}
      </div>
    ),
    mobileRender: (c) => c.author_name || "—",
  },
  {
    key: "when",
    header: "When",
    headerClassName: "w-24",
    priority: 3,
    render: (c) => (
      <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {c.committed_at ? formatRelativeTime(c.committed_at) : "—"}
      </span>
    ),
  },
  {
    key: "lines",
    header: "Lines",
    headerClassName: "w-24",
    priority: 3,
    render: (c) => (
      <span className="text-xs tabular-nums">
        <span className="text-[var(--color-success)]">+{formatLOC(c.lines_added)}</span>{" "}
        <span className="text-[var(--color-error)]">-{formatLOC(c.lines_deleted)}</span>
      </span>
    ),
  },
  {
    key: "risk",
    header: "Review priority",
    headerClassName: "w-28",
    // No ChurnBar. The cell was carrying the same fact three times over (a
    // filled bar, the percentile, and the pill), and because the queue sorts
    // by risk the bar was full-width and semantic-red on every visible row —
    // decoration that could not vary, drawn in the loudest colour available.
    // The number and the pill say it, and only one of them needs ink.
    render: (c) => (
      <div className="flex items-center gap-2">
        <span className="w-8 text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {Math.round(c.risk_percentile)}th
        </span>
        <PriorityBadge priority={c.review_priority as ReviewPriority} />
      </div>
    ),
    mobileRender: (c) => (
      <span className="inline-flex items-center gap-2">
        <span className="tabular-nums">{Math.round(c.risk_percentile)}th</span>
        <PriorityBadge priority={c.review_priority as ReviewPriority} />
      </span>
    ),
  },
];

/**
 * The review-priority queue: per-commit change-risk, ranked. Ordering is
 * server-driven (risk vs date); search + priority filters are client-side over
 * the loaded page. Review priority is shown as a **repo-relative** percentile + category,
 * so the column is portable across repos.
 */
export function CommitTable({
  commits,
  sort,
  onSortChange,
  authorship = "all",
  onAuthorshipChange,
  kind = "all",
  onKindChange,
  counts,
  onSelect,
  total,
  hasMore,
  loadingMore,
  onLoadMore,
}: CommitTableProps) {
  const [search, setSearch] = useState("");

  // Search stays client-side over the loaded page; the band and fix filters
  // are server-driven, so they narrow the repository rather than the page.
  const filtered = useMemo(() => {
    if (!search) return commits;
    const q = search.toLowerCase();
    return commits.filter(
      (c) =>
        c.subject.toLowerCase().includes(q) ||
        c.author_name.toLowerCase().includes(q) ||
        c.sha.toLowerCase().includes(q),
    );
  }, [commits, search]);

  if (commits.length === 0 && authorship === "all") {
    return (
      <EmptyState
        title="No commits indexed"
        description="Per-commit change-risk is captured on the next full index of this repo."
      />
    );
  }

  const filters: { key: CommitKind; label: string; count: number | undefined }[] = [
    { key: "all", label: "All", count: counts?.all },
    { key: "high", label: "High priority", count: counts?.high },
    { key: "fixes", label: "Fixes", count: counts?.fixes },
  ];

  const sorts: { key: CommitSort; label: string }[] = [
    { key: "risk", label: "Review priority" },
    { key: "date", label: "Most recent" },
  ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
          <Input
            placeholder="Search commits or authors…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 h-8 w-full sm:w-56 text-xs"
          />
        </div>
        <div className="flex flex-wrap rounded-md border border-[var(--color-border-default)] overflow-hidden text-xs">
          {filters.map((f) => (
            <button
              key={f.key}
              onClick={() => onKindChange?.(f.key)}
              aria-pressed={kind === f.key}
              className={cn(
                "px-2.5 py-1.5 font-medium transition-colors",
                kind === f.key
                  ? "bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
                  : "bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
              )}
            >
              {f.label}
              {f.count != null && (
                <span className="ml-1 text-[10px] opacity-70">
                  ({f.count.toLocaleString()})
                </span>
              )}
            </button>
          ))}
        </div>
        {onAuthorshipChange && (
          <div className="flex rounded-md border border-[var(--color-border-default)] overflow-hidden text-xs">
            {(
              [
                { key: "all", label: "Everyone" },
                { key: "human", label: "Humans" },
                { key: "agent", label: "Agents" },
              ] as { key: CommitAuthorship; label: string }[]
            ).map((a) => (
              <button
                key={a.key}
                onClick={() => onAuthorshipChange(a.key)}
                aria-pressed={authorship === a.key}
                className={cn(
                  "px-2.5 py-1.5 font-medium transition-colors",
                  authorship === a.key
                    ? "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)]"
                    : "bg-transparent text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)]",
                )}
              >
                {a.label}
              </button>
            ))}
          </div>
        )}
        <div className="ml-auto flex rounded-md border border-[var(--color-border-default)] overflow-hidden text-xs">
          {sorts.map((s) => (
            <button
              key={s.key}
              onClick={() => onSortChange?.(s.key)}
              aria-pressed={sort === s.key}
              className={cn(
                "px-2.5 py-1.5 font-medium transition-colors",
                sort === s.key
                  ? "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)]"
                  : "bg-transparent text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)]",
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title="No matches" description="Try adjusting your search or filters." />
      ) : (
        <div className="rounded-lg border border-[var(--color-border-default)] overflow-hidden">
          <ResponsiveTable
            columns={COLUMNS}
            rows={filtered.map((c, i) => ({ ...c, _idx: i }))}
            rowKey={(c) => c.sha}
            caption="Commit review-priority queue"
            {...(onSelect ? { onRowClick: onSelect } : {})}
            stacked="sm"
            bare
          />
          {total != null && (
            <ResultsFooter
              shown={filtered.length}
              total={total}
              hasMore={!!hasMore}
              loading={loadingMore}
              onLoadMore={onLoadMore}
              noun="commits"
            />
          )}
        </div>
      )}
    </div>
  );
}
