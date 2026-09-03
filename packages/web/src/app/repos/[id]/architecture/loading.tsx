import { SkeletonRegion, Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * Architecture is a full-height canvas rather than a `PageShell`, so this
 * mirrors that frame instead: the tab row at its real padding, then the
 * canvas filling the rest. Matching `h-full` / `flex-1` matters more here
 * than elsewhere, because the canvas below sizes itself from this box.
 */
export default function ArchitectureLoading() {
  return (
    <SkeletonRegion
      className="flex h-full flex-col"
      label="Loading architecture"
    >
      <div className="shrink-0 px-4 pt-3 sm:px-6">
        <Skeleton className="h-9 w-80 max-w-full rounded-lg" />
      </div>
      <div className="min-h-0 flex-1 p-4 sm:p-6">
        <Skeleton className="h-full w-full rounded-lg" />
      </div>
    </SkeletonRegion>
  );
}
