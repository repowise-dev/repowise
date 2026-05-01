import { Skeleton } from "@repowise/ui/ui/skeleton";

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
      {/* Table with header + rows matching content structure */}
      <div className="rounded-lg border border-[var(--color-border-default)] overflow-hidden">
        <div className="bg-[var(--color-bg-elevated)] px-4 py-2.5 border-b border-[var(--color-border-default)]">
          <div className="flex gap-6">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3 w-10" />
            <Skeleton className="h-3 w-16 hidden sm:block" />
            <Skeleton className="h-3 w-24 hidden sm:block" />
            <Skeleton className="h-3 w-16 hidden sm:block" />
          </div>
        </div>
        {Array.from({ length: 10 }).map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-6 px-4 py-3 border-b border-[var(--color-border-default)] last:border-0"
          >
            <Skeleton className="h-1.5 w-16 rounded-full" />
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-5 w-14 rounded-md" />
            <Skeleton className="h-4 w-16 hidden sm:block" />
            <Skeleton className="h-4 w-36 hidden sm:block" />
            <Skeleton className="h-4 w-8 hidden sm:block" />
          </div>
        ))}
      </div>
    </div>
  );
}
