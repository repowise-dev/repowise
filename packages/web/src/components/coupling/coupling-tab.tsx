"use client";

import useSWR from "swr";
import Link from "next/link";
import { useQueryState } from "nuqs";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { CouplingExplorer } from "@repowise-dev/ui/coupling";
import { getCoupling } from "@/lib/api/coupling";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";

/**
 * Self-fetching host for the change-coupling Architecture tab. The Architecture
 * page is a client component, so coupling data is fetched here via SWR (mirrors
 * how the impact analyzer self-fetches) rather than on the server. The whole
 * diagram + table interaction lives in `@repowise-dev/ui/coupling` so package
 * bumps propagate it to hosted; this host only supplies the repo link prefix,
 * Next's Link, and `?focus=` URL sync for the pinned file.
 */
export function CouplingTab({ repoId }: { repoId: string }) {
  const { data, error, isLoading, mutate } = useSWR(
    `coupling:${repoId}`,
    () => getCoupling(repoId, { limit: 200 }),
    { revalidateOnFocus: false },
  );
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
          LinkComponent={Link}
          // Absent `?focus=` → let the explorer open on the most-coupled hub.
          initialFocus={focus ?? undefined}
          onFocusChange={(path) => void setFocus(path)}
        />
      )}
    </div>
  );
}
