import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

export default function DeadCodeLoading() {
  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Skeleton className="h-7 w-32" />
          <Skeleton className="h-4 w-52 mt-1.5" />
        </div>
        <Skeleton className="h-9 w-28" />
      </div>
      <Skeleton className="h-20 rounded-lg" />
      <div className="flex gap-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-28" />
        ))}
      </div>
      <div className="space-y-px">
        <Skeleton className="h-10 rounded-t-lg" />
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-14 rounded-none" />
        ))}
        <Skeleton className="h-14 rounded-b-lg" />
      </div>
    </div>
  );
}
