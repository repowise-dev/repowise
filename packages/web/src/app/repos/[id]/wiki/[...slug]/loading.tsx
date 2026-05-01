import { Skeleton } from "@repowise/ui/ui/skeleton";

export default function WikiPageLoading() {
  return (
    <div className="flex h-full min-h-0">
      <div className="flex-1 min-w-0 overflow-auto">
        {/* Top bar */}
        <div className="sticky top-0 flex items-center gap-3 border-b border-[var(--color-border-default)] px-4 sm:px-6 py-2.5">
          <Skeleton className="h-4 flex-1 max-w-xs" />
          <Skeleton className="h-6 w-14" />
          <Skeleton className="h-6 w-24 hidden sm:block" />
          <Skeleton className="h-8 w-24" />
        </div>
        {/* Content */}
        <div className="px-4 sm:px-6 py-6 max-w-[768px] mx-auto space-y-4">
          <Skeleton className="h-8 w-3/4" />
          <div className="space-y-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className={`h-4 ${i % 3 === 2 ? "w-2/3" : "w-full"}`} />
            ))}
          </div>
          <Skeleton className="h-32 w-full rounded-lg" />
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-4 w-full" />
            ))}
          </div>
        </div>
      </div>
      {/* Context panel skeleton */}
      <div className="hidden xl:flex flex-col border-l border-[var(--color-border-default)] w-[280px] shrink-0">
        <div className="p-4 space-y-4">
          <Skeleton className="h-4 w-24" />
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-4 w-full" />
          ))}
        </div>
      </div>
    </div>
  );
}
