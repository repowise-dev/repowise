"use client";

import { useCallback } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useQueryState, useQueryStates } from "nuqs";
import { useTranslations } from "next-intl";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import {
  ALL_FILE_PAIRS,
  CouplingAreaView,
  toCouplingFilters,
  type AreaPairRef,
  type CouplingAreaFilters,
} from "@repowise-dev/ui/coupling";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import type { CouplingFilters } from "@repowise-dev/types/coupling";
import { getCouplingAreas } from "@/lib/api/coupling";
import { getWorkspace } from "@/lib/api/workspace";
import { useRepo } from "@/lib/hooks/use-repo";
import { useAreaCouplingCopy } from "./use-area-coupling-copy";
import { CouplingPairPanel, LoadingRows } from "./coupling-pair-panel";
import {
  couplingUrlParsers,
  filtersFromUrl,
  filtersToUrl,
  selectionFromUrl,
  selectionToUrl,
} from "./coupling-url";

type Selection = AreaPairRef | typeof ALL_FILE_PAIRS | null;

/**
 * The file-pair query for the open selection, placed at the depth the list
 * was grouped at so the pair holds exactly the files its row counted; `null`
 * with nothing open or while that depth is not settled.
 */
function pairQueryFor(selection: Selection, wire: CouplingFilters, depth: number | undefined) {
  if (!selection || !depth) return null;
  if (selection === ALL_FILE_PAIRS) return { ...wire, depth };
  return { ...wire, depth, area: selection.source, partner: selection.target };
}

/**
 * Self-fetching host for the change-coupling Architecture tab. It opens on
 * area pairs (`/coupling/areas`); choosing one fetches its file pairs into the
 * existing explorer. Filters and the open pair live in the URL; the shared
 * package owns every pixel, so hosted gets the same view from a package bump.
 */
export function CouplingTab({ repoId }: { repoId: string }) {
  const t = useTranslations("coupling");
  const copy = useAreaCouplingCopy();
  const [url, setUrl] = useQueryStates(couplingUrlParsers);
  const [, setFocus] = useQueryState("focus");
  const filters = filtersFromUrl(url);
  const selection = selectionFromUrl(url.pair);
  const wire = toCouplingFilters(filters);

  const areas = useSWR(["coupling-areas", repoId, wire], () => getCouplingAreas(repoId, wire), {
    revalidateOnFocus: false,
    keepPreviousData: true,
  });
  // Not while the list revalidates: the kept response may hold the old depth.
  const pairQuery = pairQueryFor(selection, wire, areas.isValidating ? undefined : areas.data?.depth);
  const { repo } = useRepo(repoId);
  const { data: workspace } = useSWR("workspace:info", () => getWorkspace(), {
    revalidateOnFocus: false,
  });

  const onFiltersChange = useCallback(
    (next: CouplingAreaFilters) => void setUrl(filtersToUrl(next)),
    [setUrl],
  );
  const onSelectionChange = useCallback(
    (next: Selection) => {
      void setFocus(null);
      void setUrl({ pair: selectionToUrl(next) });
    },
    [setUrl, setFocus],
  );

  if (areas.error) {
    return (
      <ApiError
        title={t("loadFailed")}
        message={toFriendlyMessage(areas.error)}
        onRetry={() => void areas.mutate()}
      />
    );
  }
  if (!areas.data) return <LoadingRows />;

  return (
    <CouplingAreaView
      data={areas.data}
      filters={filters}
      onFiltersChange={onFiltersChange}
      selection={selection}
      onSelectionChange={onSelectionChange}
      {...(repo?.name ? { repoName: repo.name } : {})}
      {...(workspace?.is_workspace ? { workspaceHref: "/workspace/co-changes" } : {})}
      LinkComponent={Link}
      copy={copy}
    >
      {pairQuery ? (
        <CouplingPairPanel key={url.pair ?? ""} repoId={repoId} repoName={repo?.name} query={pairQuery} />
      ) : (
        <LoadingRows />
      )}
    </CouplingAreaView>
  );
}
