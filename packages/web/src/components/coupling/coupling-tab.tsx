"use client";

import useSWR from "swr";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { getCoupling } from "@/lib/api/coupling";
import { CouplingView } from "@/components/coupling/coupling-view";

/**
 * Self-fetching host for the change-coupling Architecture tab. The Architecture
 * page is a client component, so coupling data is fetched here via SWR (mirrors
 * how the impact analyzer self-fetches) rather than on the server. The diagram
 * and table stay in `@repowise-dev/ui/coupling` so package bumps propagate them.
 */
export function CouplingTab({ repoId }: { repoId: string }) {
  const { data, error, isLoading, mutate } = useSWR(
    `coupling:${repoId}`,
    () => getCoupling(repoId, { limit: 200 }),
    { revalidateOnFocus: false },
  );

  return (
    <div className="space-y-6">
      {error ? (
        <ApiError
          title="Couldn't load change coupling"
          message={error instanceof Error ? error.message : String(error)}
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
        <CouplingView data={data} />
      )}
    </div>
  );
}
