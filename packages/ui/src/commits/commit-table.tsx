"use client";

import { useMemo, useState } from "react";
import { Bug, Search } from "lucide-react";
import { AgentBadge, NewContributorBadge } from "./agent-badge";
import { PriorityBadge } from "./priority-badge";
import { ChurnBar } from "../git/churn-bar";
import { Input } from "../ui/input";
import { EmptyState } from "../shared/empty-state";
import { ResultsFooter } from "../shared/results-footer";
import { formatLOC, formatRelativeTime } from "../lib/format";
import { cn } from "../lib/cn";
import type { Commit, ReviewPriority } from "@repowise-dev/types/git";

export type CommitSort = "risk" | "date";
export type CommitAuthorship = "all" | "human" | "agent";

/** Below this many prior commits the author gets a "new contributor" badge. */
const NEW_CONTRIBUTOR_THRESHOLD = 3;

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
        <div className="rounded-lg border border-[var(--color-border-default)] overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="sticky top-0 z-10 bg-[var(--color-bg-elevated)]">
              <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-8">
                  #
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                  Commit
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden md:table-cell">
                  Author
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24 hidden lg:table-cell">
                  When
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24 hidden lg:table-cell">
                  Lines
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-40">
                  Risk
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden xl:table-cell">
                  Top driver
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c, i) => (
                <tr
                  key={c.sha}
                  onClick={onSelect ? () => onSelect(c) : undefined}
                  className={cn(
                    "border-b border-[var(--color-border-default)] last:border-0 hover:bg-[var(--color-bg-elevated)] transition-colors",
                    onSelect && "cursor-pointer",
                  )}
                >
                  <td className="px-3 py-2.5 text-[var(--color-text-tertiary)] tabular-nums text-xs">
                    {i + 1}
                  </td>
                  <td className="px-3 py-2.5 min-w-[200px] max-w-[460px]">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-[var(--color-text-tertiary)] shrink-0">
                        {c.short_sha}
                      </span>
                      {c.is_fix && <Bug className="h-3 w-3 shrink-0 text-[var(--color-error)]" />}
                      <span
                        className="truncate text-xs text-[var(--color-text-primary)]"
                        title={c.subject}
                      >
                        {c.subject || "(no subject)"}
                      </span>
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-[var(--color-text-secondary)] hidden md:table-cell max-w-[220px]">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="truncate">{c.author_name || "—"}</span>
                      {c.agent_name && (
                        <AgentBadge agentName={c.agent_name} tier={c.agent_autonomy_tier} />
                      )}
                      {!c.agent_name &&
                        c.author_experience != null &&
                        c.author_experience < NEW_CONTRIBUTOR_THRESHOLD && (
                          <NewContributorBadge experience={c.author_experience} />
                        )}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-[var(--color-text-tertiary)] tabular-nums hidden lg:table-cell">
                    {c.committed_at ? formatRelativeTime(c.committed_at) : "—"}
                  </td>
                  <td className="px-3 py-2.5 text-xs tabular-nums hidden lg:table-cell">
                    <span className="text-[var(--color-success)]">+{formatLOC(c.lines_added)}</span>{" "}
                    <span className="text-[var(--color-error)]">-{formatLOC(c.lines_deleted)}</span>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <ChurnBar percentile={c.risk_percentile} className="w-16" />
                      <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8">
                        {Math.round(c.risk_percentile)}%
                      </span>
                      <PriorityBadge priority={c.review_priority as ReviewPriority} />
                    </div>
                  </td>
                  <td
                    className="px-3 py-2.5 text-xs text-[var(--color-text-tertiary)] hidden xl:table-cell max-w-[220px] truncate"
                    title={c.top_driver ?? undefined}
                  >
                    {c.top_driver ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
