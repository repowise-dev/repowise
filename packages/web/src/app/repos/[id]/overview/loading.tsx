import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

/** Progressive skeleton mirroring the Overview layout — header, attention
 *  strip, stat strip, tab area. (The owl stays reserved for brand moments.) */
export default function OverviewLoading() {
  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <Skeleton className="h-7 w-48" />
          <Skeleton className="h-5 w-16" />
          <Skeleton className="h-7 w-40 rounded-full" />
        </div>
        <Skeleton className="h-4 w-72" />
        <Skeleton className="h-9 w-80" />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Skeleton className="h-56 lg:col-span-2" />
        <div className="space-y-4">
          <Skeleton className="h-32" />
          <Skeleton className="h-32" />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-16" />
        ))}
      </div>
      <Skeleton className="h-72" />
    </div>
  );
}
