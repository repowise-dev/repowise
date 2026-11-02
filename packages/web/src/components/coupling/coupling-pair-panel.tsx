"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useQueryState } from "nuqs";
import { useTranslations } from "next-intl";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { CouplingExplorer } from "@repowise-dev/ui/coupling";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import type { CouplingFilters } from "@repowise-dev/types/coupling";
import { getCoupling } from "@/lib/api/coupling";

/** The route's own ceiling. Asking beyond it is rejected, not clamped. */
const MAX_LIMIT = 1000;
const INITIAL_LIMIT = 200;

export function LoadingRows() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-12" />
      ))}
    </div>
  );
}

export interface CouplingPairPanelProps {
  repoId: string;
  repoName?: string | undefined;
  /** The list's filters at the depth it was grouped at, plus the pair's two areas. */
  query: CouplingFilters & { area?: string; partner?: string };
}

/**
 * One area pair's file pairs (or every file pair) in the existing explorer.
 * The host keys it by the open pair, so the page size and the pinned file
 * reset when the reader opens another pair; "Load more" keeps the explorer
 * mounted while the larger page loads.
 */
export function CouplingPairPanel({ repoId, repoName, query }: CouplingPairPanelProps) {
  const t = useTranslations("coupling");
  const [limit, setLimit] = useState(INITIAL_LIMIT);
  const [focus, setFocus] = useQueryState("focus");
  const { data, error, isValidating, mutate } = useSWR(
    ["coupling", repoId, query, limit],
    () => getCoupling(repoId, { ...query, limit }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  if (error) {
    return (
      <ApiError title={t("loadFailed")} message={toFriendlyMessage(error)} onRetry={() => void mutate()} />
    );
  }
  if (!data) return <LoadingRows />;
  return (
    <CouplingExplorer
      data={data}
      repoLinkPrefix={`/repos/${repoId}`}
      {...(repoName ? { repoName } : {})}
      LinkComponent={Link}
      // Absent / bare `?focus=` → let the explorer open on the most-coupled hub.
      initialFocus={focus || undefined}
      onFocusChange={(value) => void setFocus(value)}
      {...(limit < MAX_LIMIT ? { onShowMore: () => setLimit(MAX_LIMIT) } : {})}
      loadingMore={isValidating}
    />
  );
}
