"use client";

/**
 * Hotspots, as a section under the Code Health map rather than a tab of its own.
 *
 * Churn is a lens on the map; what this section adds is the ranked list itself,
 * in the map's scope (tests, docs examples, build, vendored and generated files
 * held out unless `?include=` brings them back). The server searches, filters,
 * sorts and counts, so every chip and total describes the whole list, not the
 * page that happens to be loaded. Search, filter and sort live in the URL.
 */

import { useCallback } from "react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { OverviewSection, SectionLink } from "@repowise-dev/ui/overview";
import { HotspotTable } from "@repowise-dev/ui/git/hotspot-table";
import {
  buildHotspotAiPrompt,
  buildHotspotBatchAiPrompt,
  fileChatContext,
  useAiPromptAction,
  type HotspotPromptInput,
} from "@repowise-dev/ui/health";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { hotspotToFileCard } from "@repowise-dev/ui/shared/file-card";
import type { Hotspot } from "@repowise-dev/types/git";
import { useFileCardHost } from "@/components/shared/file-card-host";
import { useHotspotList } from "./use-hotspot-list";

/** The fields the hotspot prompt reads; the row carries more than it needs. */
function promptInput(h: Hotspot): HotspotPromptInput {
  return {
    file_path: h.file_path,
    churn_percentile: h.churn_percentile,
    commit_count_90d: h.commit_count_90d,
    commit_count_30d: h.commit_count_30d,
    bus_factor: h.bus_factor,
    contributor_count: h.contributor_count,
    primary_owner: h.primary_owner,
    lines_added_90d: h.lines_added_90d,
    lines_deleted_90d: h.lines_deleted_90d,
    temporal_hotspot_score: h.temporal_hotspot_score ?? null,
    change_entropy_pct: h.change_entropy_pct ?? null,
    prior_defect_count: h.prior_defect_count ?? null,
  };
}

export function HotspotsSection({ repoId }: { repoId: string }) {
  const t = useTranslations("codeHealth");
  const { showFile, dialog } = useFileCardHost(repoId);
  const { openPrompt, promptModal } = useAiPromptAction();
  const list = useHotspotList(repoId);

  const promptFor = useCallback(
    (h: Hotspot) =>
      openPrompt({
        title: t("hotspots.promptTitle"),
        description: t("hotspots.promptDescription"),
        filePath: h.file_path,
        getPrompt: (flavor) => buildHotspotAiPrompt({ hotspot: promptInput(h), flavor }),
        chatContext: fileChatContext(h.file_path),
      }),
    [openPrompt, t],
  );
  const { total } = list;
  const promptForRows = useCallback(
    (rows: Hotspot[]) =>
      openPrompt({
        title: t("hotspots.batchPromptTitle", { count: total }),
        description: t("hotspots.batchPromptDescription"),
        getPrompt: (flavor) =>
          buildHotspotBatchAiPrompt({ rows: rows.map(promptInput), total, flavor }),
      }),
    [openPrompt, t, total],
  );
  const select = useCallback((h: Hotspot) => showFile(hotspotToFileCard(h)), [showFile]);

  const narrowed = list.query !== "" || list.filter !== "all";
  // A repo with no git history to mine shows no section, rather than an empty
  // state explaining an absence the reader cannot act on.
  if (!list.isLoading && !list.error && list.items.length === 0 && !narrowed) return null;

  return (
    <OverviewSection
      title={t("hotspots.title")}
      description={t("hotspots.description")}
      action={
        <SectionLink href={`/repos/${repoId}/commits`} LinkComponent={Link}>
          {t("hotspots.commitHistory")}
        </SectionLink>
      }
    >
      {list.error && list.items.length === 0 ? (
        <p className="text-sm text-[var(--color-text-secondary)]">{t("hotspots.loadFailed")}</p>
      ) : list.isLoading && list.items.length === 0 ? (
        <Skeleton className="h-72 w-full rounded-xl" />
      ) : (
        <HotspotTable
          hotspots={list.items}
          total={list.total}
          {...(list.facets ? { facets: list.facets } : {})}
          query={list.query}
          onQueryChange={list.setQuery}
          filter={list.filter}
          onFilterChange={list.setFilter}
          sort={list.sort}
          onSortChange={list.setSort}
          hasMore={list.hasMore}
          loadingMore={list.loadingMore}
          onLoadMore={list.loadMore}
          onSelect={select}
          onGeneratePrompt={promptFor}
          onGenerateBatchPrompt={promptForRows}
          batchPromptLabel={(count) => t("hotspots.batchPromptAction", { count })}
        />
      )}
      {dialog}
      {promptModal}
    </OverviewSection>
  );
}
