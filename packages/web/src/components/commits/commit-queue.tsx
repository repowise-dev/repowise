"use client";

import { useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQueryState } from "nuqs";
import { useTranslations } from "next-intl";
import { CommitTable } from "@repowise-dev/ui/commits/commit-table";
import {
  HistoryFilters,
  type HealthImpact,
  type HistoryFilterValue,
} from "@repowise-dev/ui/commits/history-filters";
import type { HistoryFilters as ApiHistoryFilters } from "@repowise-dev/types/history";
import type { CommitResponse, Paginated } from "@/lib/api/types";
import { commitFiltersQuery } from "@/lib/commit-filters";
import { useHealthDeltaLabels, useHistoryFilterLabels } from "./history-labels";
import { useCommitPages, type CommitListQuery } from "./use-commit-pages";

/**
 * The commit list: the history filters, then every commit led by what it did
 * to code health.
 *
 * The history filters are URL state and server-driven: a change writes the
 * query string and the page re-renders on the server, so the headline, the
 * wins and this list's first page arrive together for the same filters.
 * `apiFilters` is that server's reading of the URL (its clock picks the
 * window), passed down so every later page asks for the same range. Sort,
 * review priority and authorship stay local refinements of the list.
 * Selecting a row writes `?commit=` for the detail sheet, a separate island.
 */
export function CommitQueue({
  repoId,
  initial,
  counts,
  filters,
  apiFilters,
  impactCounts,
  narrowed,
  attributionEnabled = true,
}: {
  repoId: string;
  initial: Paginated<CommitResponse>;
  /** Repo-wide chip counts; only meaningful when nothing narrows the list. */
  counts?: { all: number; high: number; fixes: number } | undefined;
  filters: HistoryFilterValue;
  apiFilters: ApiHistoryFilters;
  /** Per-impact counts under the other filters, from the period summary. */
  impactCounts?: Record<HealthImpact, number> | undefined;
  narrowed: boolean;
  /** False when AI attribution is off for the repo: no authorship filter. */
  attributionEnabled?: boolean;
}) {
  const [query, setQuery] = useState<CommitListQuery>({ sort: "date", authorship: "all", kind: "all" });
  const [, setSelectedSha] = useQueryState("commit");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [pending, startTransition] = useTransition();
  const t = useTranslations("views.commits");
  const healthLabels = useHealthDeltaLabels();
  const filterLabels = useHistoryFilterLabels();
  const pages = useCommitPages(repoId, apiFilters, query, initial);

  const refine = (patch: Partial<CommitListQuery>) => {
    setQuery((q) => ({ ...q, ...patch }));
    pages.reset();
  };

  const applyFilters = (patch: Partial<HistoryFilterValue>) => {
    const next = commitFiltersQuery(new URLSearchParams(searchParams.toString()), patch);
    pages.reset();
    startTransition(() => router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false }));
  };

  return (
    <div className="space-y-4">
      <HistoryFilters value={filters} onChange={applyFilters} counts={impactCounts} labels={filterLabels} />
      <div aria-busy={pending} className={pending ? "opacity-60 transition-opacity" : undefined}>
        <CommitTable
          commits={pages.commits}
          sort={query.sort}
          onSortChange={(sort) => refine({ sort })}
          authorship={query.authorship}
          onAuthorshipChange={attributionEnabled ? (authorship) => refine({ authorship }) : undefined}
          kind={query.kind}
          onKindChange={(kind) => refine({ kind })}
          counts={counts}
          onSelect={(c) => void setSelectedSha(c.sha)}
          total={pages.total}
          hasMore={pages.hasMore}
          loadingMore={pages.loadingMore}
          onLoadMore={pages.loadMore}
          healthLabels={healthLabels}
          hotspotLabel={t("hotspotMark")}
          narrowed={narrowed}
        />
      </div>
    </div>
  );
}
