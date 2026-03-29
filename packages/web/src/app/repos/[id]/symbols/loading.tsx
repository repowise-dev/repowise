import { Skeleton } from "@/components/ui/skeleton";

export default function SymbolsLoading() {
  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <Skeleton className="h-7 w-36" />
        <Skeleton className="h-4 w-56 mt-1.5" />
      </div>
      {/* Filter row */}
      <div className="flex flex-wrap gap-2">
        <Skeleton className="h-9 w-48" />
        <Skeleton className="h-9 w-32" />
        <Skeleton className="h-9 w-32" />
      </div>
      {/* Table rows */}
      <div className="space-y-px">
        <Skeleton className="h-10 rounded-t-lg" />
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-12 rounded-none" />
        ))}
        <Skeleton className="h-12 rounded-b-lg" />
      </div>
    </div>
  );
}
