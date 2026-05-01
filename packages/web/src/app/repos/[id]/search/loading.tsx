import { Skeleton } from "@repowise/ui/ui/skeleton";

export default function SearchLoading() {
  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-3xl">
      <div>
        <Skeleton className="h-7 w-24" />
        <Skeleton className="h-4 w-56 mt-1.5" />
      </div>
      <div className="space-y-3">
        <Skeleton className="h-11 rounded-lg" />
        <div className="flex gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-24" />
          ))}
        </div>
      </div>
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-lg" />
        ))}
      </div>
    </div>
  );
}
