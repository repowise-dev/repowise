"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useQueryState } from "nuqs";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { CouplingExplorer } from "@repowise-dev/ui/coupling";
import { getCoupling } from "@/lib/api/coupling";
import { useRepo } from "@/lib/hooks/use-repo";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";

/** The route's own ceiling. Asking beyond it is rejected, not clamped. */
const MAX_LIMIT = 1000;
const INITIAL_LIMIT = 200;

/**
 * Self-fetching host for the change-coupling Architecture tab. The Architecture
 * page is a client component, so coupling data is fetched here via SWR (mirrors
 * how the impact analyzer self-fetches) rather than on the server. The whole
 * diagram + table interaction lives in `@repowise-dev/ui/coupling` so package
 * bumps propagate it to hosted; this host only supplies the repo link prefix,
 * Next's Link, `?focus=` URL sync for the pinned selection, and the cap.
 *
 * The cap starts at 200 and the explorer offers to raise it to the route's
 * ceiling, so the "showing N of M" line is never a dead end.
 */
export function CouplingTab({ repoId }: { repoId: string }) {
  const [limit, setLimit] = useState(INITIAL_LIMIT);
  const { data, error, isLoading, isValidating, mutate } = useSWR(
    `coupling:${repoId}:${limit}`,
    () => getCoupling(repoId, { limit }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );
  // Already fetched by the page shell; SWR dedupes, so this is a cache read.
  const { repo } = useRepo(repoId);
  const [focus, setFocus] = useQueryState("focus");

  return (
    <div className="space-y-6">
      {error ? (
        <ApiError
          title="Couldn't load change coupling"
          message={toFriendlyMessage(error)}
          onRetry={() => void mutate()}
        />
      ) : isLoading || !data ? (
        <div className="space-y-4">
          <Skeleton className="mx-auto h-[420px] w-full max-w-[820px] rounded-xl" />
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        </div>
      ) : (
        <CouplingExplorer
          data={data}
          repoLinkPrefix={`/repos/${repoId}`}
          {...(repo?.name ? { repoName: repo.name } : {})}
          LinkComponent={Link}
          // Absent / bare `?focus=` → let the explorer open on the most-coupled hub.
          initialFocus={focus || undefined}
          onFocusChange={(value) => void setFocus(value)}
          {...(limit < MAX_LIMIT ? { onShowMore: () => setLimit(MAX_LIMIT) } : {})}
          loadingMore={isValidating}
        />
      )}
    </div>
  );
}
