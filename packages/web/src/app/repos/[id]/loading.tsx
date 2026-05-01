import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

export default function RepoLoading() {
  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1200px]">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Skeleton className="h-7 w-48" />
          <Skeleton className="h-4 w-64 mt-1.5" />
        </div>
        <div className="flex gap-2 shrink-0">
          <Skeleton className="h-6 w-16" />
          <Skeleton className="h-6 w-16" />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-36 rounded-lg" />
      <Skeleton className="h-48 rounded-lg" />
    </div>
  );
}
