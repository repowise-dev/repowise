import { StatGridSkeleton } from "@repowise-dev/ui/shared/loading-skeletons";
import { SkeletonRegion, Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * Mirrors the docs coverage layout: the docs header band, then the donut
 * beside its three stat cards, then the page list. The cards are real
 * `MetricCard` boxes via `StatGridSkeleton`, so they do not resize on
 * arrival the way a guessed height would.
 */
export default function DocsCoverageLoading() {
  return (
    <SkeletonRegion className="flex h-full flex-col" label="Loading coverage">
      <div className="shrink-0 border-b border-[var(--color-border-default)] px-4 py-3 sm:px-6">
        <Skeleton className="h-6 w-56 max-w-full" />
      </div>
      <div className="max-w-[1600px] flex-1 space-y-6 p-4 sm:p-6">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
          <div className="flex shrink-0 flex-col items-center gap-4 lg:w-56">
            <Skeleton className="h-40 w-40 rounded-full" />
            <Skeleton className="h-5 w-40" />
          </div>
          <StatGridSkeleton count={3} className="flex-1" />
        </div>
        <Skeleton className="h-72 w-full rounded-xl" />
      </div>
    </SkeletonRegion>
  );
}
