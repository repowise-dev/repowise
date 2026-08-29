import { SkeletonRegion, Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * The shell itself, waiting. This is the one place with no page layout to
 * mirror, because the shell is what has not painted yet: it stands in for the
 * sidebar rail and the content column, at the sidebar's own expanded width.
 *
 * It replaces `fallback={null}`, which showed nothing at all while the shell
 * suspended, so a slow first paint looked like a blank app rather than a
 * loading one.
 */
export function AppShellSkeleton() {
  return (
    <SkeletonRegion
      className="flex h-screen overflow-hidden"
      label="Loading Repowise"
    >
      {/* Matches Sidebar's expanded width (`w-[280px]`). */}
      <div className="hidden w-[280px] shrink-0 flex-col gap-2 border-r border-[var(--color-border-default)] p-4 md:flex">
        <Skeleton className="h-8 w-32" />
        <div className="mt-4 space-y-1.5">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
        <Skeleton className="mt-4 h-3 w-24" />
        <div className="space-y-1.5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      </div>
      <div className="flex-1 p-[var(--page-pad)]">
        <Skeleton className="h-full w-full rounded-xl" />
      </div>
    </SkeletonRegion>
  );
}
