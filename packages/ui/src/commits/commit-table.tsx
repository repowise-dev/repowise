"use client";

import { useMemo, useState } from "react";
import { Bug, Search } from "lucide-react";
import { AgentBadge, NewContributorBadge, isNewContributor } from "./agent-badge";
import { PriorityBadge } from "./priority-badge";
import { ChurnBar } from "../git/churn-bar";
import { Input } from "../ui/input";
import { EmptyState } from "../shared/empty-state";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { ResultsFooter } from "../shared/results-footer";
import { formatLOC, formatRelativeTime } from "../lib/format";
import { cn } from "../lib/cn";
import type { Commit, ReviewPriority } from "@repowise-dev/types/git";

export type CommitSort = "risk" | "date";
export type CommitAuthorship = "all" | "human" | "agent";

export interface CommitTableProps {
  commits: Commit[];
  /** Server-driven ordering. `risk` = review-priority order, `date` = recency. */
  sort: CommitSort;
  onSortChange?: (sort: CommitSort) => void;
  /** Server-driven authorship filter (agent provenance). Control omitted when
   *  no handler is provided. */
  authorship?: CommitAuthorship;
  onAuthorshipChange?: (authorship: CommitAuthorship) => void;
  onSelect?: (commit: Commit) => void;
  total?: number;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
}

type Filter = "all" | "high" | "fixes";

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
        <span className="truncate text-xs text-[var(--color-text-primary)]" title={c.subject}>
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
    header: "Risk",
    headerClassName: "w-40",
    render: (c) => (
      <div className="flex items-center gap-2">
        <ChurnBar percentile={c.risk_percentile} className="w-16" />
        <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8">
          {Math.round(c.risk_percentile)}%
        </span>
        <PriorityBadge priority={c.review_priority as ReviewPriority} />
      </div>
    ),
    mobileRender: (c) => (
      <span className="inline-flex items-center gap-2">
        <span className="tabular-nums">{Math.round(c.risk_percentile)}%</span>
        <PriorityBadge priority={c.review_priority as ReviewPriority} />
      </span>
    ),
  },
  {
    key: "top_driver",
    header: "Top driver",
    headerClassName: "max-xl:hidden",
    cellClassName: "max-xl:hidden max-w-[220px]",
    hideInCard: true,
    render: (c) => (
      <span
        className="block truncate text-xs text-[var(--color-text-tertiary)]"
        title={c.top_driver ?? undefined}
      >
        {c.top_driver ?? "—"}
      </span>
    ),
  },
];

/**
 * The review-priority queue: per-commit change-risk, ranked. Ordering is
 * server-driven (risk vs date); search + priority filters are client-side over
 * the loaded page. Risk is shown as a **repo-relative** percentile + priority,
 * so the column is portable across repos.
 */
export function CommitTable({
  commits,
  sort,
  onSortChange,
  authorship = "all",
  onAuthorshipChange,
  onSelect,
  total,
  hasMore,
  loadingMore,
  onLoadMore,
}: CommitTableProps) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const filtered = useMemo(() => {
    let items = commits;
    if (search) {
      const q = search.toLowerCase();
      items = items.filter(
        (c) =>
          c.subject.toLowerCase().includes(q) ||
          c.author_name.toLowerCase().includes(q) ||
          c.sha.toLowerCase().includes(q),
      );
    }
    if (filter === "high") items = items.filter((c) => c.review_priority === "high");
    else if (filter === "fixes") items = items.filter((c) => c.is_fix);
    return items;
  }, [commits, search, filter]);

  if (commits.length === 0 && authorship === "all") {
    return (
      <EmptyState
        title="No commits indexed"
        description="Per-commit change-risk is captured on the next full index of this repo."
      />
    );
  }

  const filters: { key: Filter; label: string; count: number }[] = [
    { key: "all", label: "All", count: commits.length },
    {
      key: "high",
      label: "High priority",
      count: commits.filter((c) => c.review_priority === "high").length,
    },
    { key: "fixes", label: "Fixes", count: commits.filter((c) => c.is_fix).length },
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
              onClick={() => setFilter(f.key)}
              className={cn(
                "px-2.5 py-1.5 font-medium transition-colors",
                filter === f.key
                  ? "bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
                  : "bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
              )}
            >
              {f.label}
              <span className="ml-1 text-[10px] opacity-70">({f.count})</span>
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
