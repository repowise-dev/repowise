"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Input } from "../ui/input";
import { EmptyState } from "../shared/empty-state";
import { ResponsiveTable } from "../shared/responsive-table";
import { ResultsFooter } from "../shared/results-footer";
import { Segmented } from "../shared/segmented";
import { cn } from "../lib/cn";
import type { Commit } from "@repowise-dev/types/git";
import type { HealthDeltaLabels } from "./health-delta-mark";
import { commitColumns } from "./commit-columns";

export type CommitSort = "risk" | "date";
export type CommitAuthorship = "all" | "human" | "agent" | "unknown";
export type CommitKind = "all" | "high" | "fixes";

/** Review priority and fixes, server-driven. `counts` are repo-wide: pass
 *  them only when nothing else narrows the list. */
export function CommitKindControl({
  value,
  onChange,
  counts,
}: {
  value: CommitKind;
  onChange: (kind: CommitKind) => void;
  counts?: { all: number; high: number; fixes: number } | undefined;
}) {
  return (
    <Segmented<CommitKind>
      label="Review priority and fixes"
      value={value}
      options={[
        { value: "all", label: "All", count: counts?.all.toLocaleString() },
        { value: "high", label: "High priority", count: counts?.high.toLocaleString() },
        { value: "fixes", label: "Fixes", count: counts?.fixes.toLocaleString() },
      ]}
      onChange={onChange}
    />
  );
}

/** Ordering, server-driven: recency, or the diff-shape review priority. */
export function CommitSortControl({
  value,
  onChange,
}: {
  value: CommitSort;
  onChange: (sort: CommitSort) => void;
}) {
  return (
    <Segmented<CommitSort>
      label="Sort"
      value={value}
      options={[
        { value: "date", label: "Most recent" },
        { value: "risk", label: "Review priority" },
      ]}
      onChange={onChange}
    />
  );
}

/** Search over the loaded commits; it narrows the page, not the repository. */
export function CommitSearch({
  value,
  onChange,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  className?: string | undefined;
}) {
  return (
    <div className={cn("relative", className)}>
      <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
      <Input
        type="search"
        aria-label="Search commits or authors"
        placeholder="Search commits or authors…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 w-full pl-8 text-xs sm:w-56"
      />
    </div>
  );
}

/** Loaded commits whose subject, author or sha contains `search`. */
export function filterCommits(commits: Commit[], search: string): Commit[] {
  const q = search.trim().toLowerCase();
  if (!q) return commits;
  return commits.filter(
    (c) =>
      c.subject.toLowerCase().includes(q) ||
      c.author_name.toLowerCase().includes(q) ||
      c.sha.toLowerCase().includes(q),
  );
}

export interface CommitListProps {
  commits: Commit[];
  /** Client-side search over the loaded page. */
  search?: string | undefined;
  /** True when a filter narrowed the list, so empty reads "no matches", not
   *  "nothing indexed". */
  narrowed?: boolean | undefined;
  onSelect?: (commit: Commit) => void;
  total?: number;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  /** Words for the health chip; the host's translations. */
  healthLabels?: Partial<HealthDeltaLabels> | undefined;
  /** Word marking a commit that made health worse in a hotspot file. */
  hotspotLabel?: string | undefined;
}

/**
 * The commits themselves, each led by what it did to code health. Controls
 * are separate pieces, so a host can place them where its page wants them.
 */
export function CommitList({
  commits,
  search = "",
  narrowed = false,
  onSelect,
  total,
  hasMore,
  loadingMore,
  onLoadMore,
  healthLabels,
  hotspotLabel = "Hotspot",
}: CommitListProps) {
  const cols = useMemo(() => commitColumns(healthLabels, hotspotLabel), [healthLabels, hotspotLabel]);
  const shown = useMemo(() => filterCommits(commits, search), [commits, search]);

  if (commits.length === 0 && !narrowed && !search.trim()) {
    return (
      <EmptyState
        title="No commits indexed"
        description="Per-commit change-risk is captured on the next full index of this repo."
      />
    );
  }
  if (shown.length === 0) {
    return <EmptyState title="No matches" description="Try adjusting your search or filters." />;
  }
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border-default)]">
      <ResponsiveTable
        columns={cols}
        rows={shown.map((c, i) => ({ ...c, _idx: i }))}
        rowKey={(c) => c.sha}
        caption="Commits, each led by what it did to code health"
        {...(onSelect ? { onRowClick: onSelect } : {})}
        stacked="sm"
        bare
      />
      {total != null && (
        <ResultsFooter
          shown={shown.length}
          total={total}
          hasMore={!!hasMore}
          loading={loadingMore}
          onLoadMore={onLoadMore}
          noun="commits"
        />
      )}
    </div>
  );
}

const AUTHORSHIP: { key: CommitAuthorship; label: string }[] = [
  { key: "all", label: "Everyone" },
  { key: "agent", label: "Agents" },
  { key: "human", label: "Human-attested" },
  { key: "unknown", label: "Unknown" },
];

export interface CommitTableProps extends Omit<CommitListProps, "search" | "narrowed"> {
  /** Server-driven ordering. `risk` = review-priority order, `date` = recency. */
  sort: CommitSort;
  onSortChange?: (sort: CommitSort) => void;
  /** Server-driven authorship filter. Control omitted without a handler. */
  authorship?: CommitAuthorship;
  onAuthorshipChange?: (authorship: CommitAuthorship) => void;
  /** Server-driven review-priority / fixes filter. */
  kind?: CommitKind;
  onKindChange?: (kind: CommitKind) => void;
  counts?: { all: number; high: number; fixes: number } | undefined;
  narrowed?: boolean | undefined;
}

/**
 * The list with its controls in one row above it, for hosts that want the
 * whole thing in one piece. The OSS Commits page composes the pieces itself.
 */
export function CommitTable({
  sort,
  onSortChange,
  authorship = "all",
  onAuthorshipChange,
  kind = "all",
  onKindChange,
  counts,
  narrowed = false,
  ...list
}: CommitTableProps) {
  const [search, setSearch] = useState("");
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <CommitSearch value={search} onChange={setSearch} />
        <CommitKindControl value={kind} onChange={(k) => onKindChange?.(k)} counts={counts} />
        {onAuthorshipChange && (
          <Segmented<CommitAuthorship>
            label="Authorship"
            value={authorship}
            options={AUTHORSHIP.map((a) => ({ value: a.key, label: a.label }))}
            onChange={onAuthorshipChange}
          />
        )}
        <div className="ml-auto">
          <CommitSortControl value={sort} onChange={(s) => onSortChange?.(s)} />
        </div>
      </div>
      <CommitList {...list} search={search} narrowed={narrowed || kind !== "all" || authorship !== "all"} />
    </div>
  );
}
