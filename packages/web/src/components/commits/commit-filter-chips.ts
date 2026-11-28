"use client";

import { useTranslations } from "next-intl";
import type { ActiveFilterChip } from "@repowise-dev/ui/shared/more-filters";
import type { HistoryFilterValue } from "@repowise-dev/ui/commits/history-filters";
import type { CommitListQuery } from "./use-commit-pages";

/**
 * Every secondary filter on the commit list (author, area, search, review
 * priority, sort) as a removable chip, plus the one action that clears them
 * all. The primary row (impact, hotspots, window) is never a chip: it is
 * always visible.
 */
export function useCommitFilterChips({
  filters,
  search,
  query,
  applyFilters,
  setSearch,
  refine,
}: {
  filters: HistoryFilterValue;
  search: string;
  query: CommitListQuery;
  applyFilters: (patch: Partial<HistoryFilterValue>) => void;
  setSearch: (value: string) => void;
  refine: (patch: Partial<CommitListQuery>) => void;
}): { chips: ActiveFilterChip[]; clearAll: () => void } {
  const t = useTranslations("views.commits.chips");
  const chips: ActiveFilterChip[] = [];
  if (filters.author) {
    chips.push({ key: "author", label: t("author", { value: filters.author }), onRemove: () => applyFilters({ author: undefined }) });
  }
  if (filters.area) {
    chips.push({ key: "area", label: t("area", { value: filters.area }), onRemove: () => applyFilters({ area: undefined }) });
  }
  if (search.trim()) {
    chips.push({ key: "search", label: t("search", { value: search.trim() }), onRemove: () => setSearch("") });
  }
  if (query.kind !== "all") {
    chips.push({ key: "kind", label: t(query.kind), onRemove: () => refine({ kind: "all" }) });
  }
  if (query.sort !== "date") {
    chips.push({ key: "sort", label: t("sortRisk"), onRemove: () => refine({ sort: "date" }) });
  }
  const clearAll = () => {
    setSearch("");
    refine({ kind: "all", sort: "date" });
    if (filters.author || filters.area) applyFilters({ author: undefined, area: undefined });
  };
  return { chips, clearAll };
}
