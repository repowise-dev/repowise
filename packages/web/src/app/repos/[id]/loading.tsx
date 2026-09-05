import { PageSkeleton } from "@repowise-dev/ui/shared/loading-skeletons";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * The Suspense fallback for every repo tab that has no `loading.tsx` of its
 * own — 24 routes at the time of writing, so this fires on essentially every
 * sidebar click.
 *
 * It used to be the owl. That put two different loading treatments in
 * sequence for one navigation: the owl for the route transition, then the
 * page's own skeleton for its data fetch. The owl is a brand moment, kept for
 * a cold start of the app shell where there is genuinely no layout to mirror.
 * Here there is a layout to mirror: every one of these pages is a `PageShell`.
 *
 * The header is exact because the frame is shared. The body is one reserved
 * block rather than a guessed composition — 24 routes differ below the
 * header, and a specific guess would reflow on arrival for most of them.
 */
export default function RepoLoading() {
  return (
    <PageSkeleton label="Loading">
      <Skeleton className="h-[60vh] min-h-80 w-full rounded-xl" />
    </PageSkeleton>
  );
}
