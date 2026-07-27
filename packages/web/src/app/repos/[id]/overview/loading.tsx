import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * Skeleton mirroring the Overview layout — identity header, change line,
 * health lede beside its reads column, scale ribbon, then sections.
 *
 * Shapes and widths track the real page deliberately: a skeleton that does not
 * match causes a visible reflow the moment content lands, which reads as
 * slower than showing nothing. (The owl stays reserved for brand moments.)
 */
export default function OverviewLoading() {
  return (
    <div className="mx-auto flex w-full max-w-[1280px] flex-col gap-6 p-[var(--page-pad)] sm:gap-8">
      {/* Identity header: mark, title, description, meta row */}
      <div className="flex gap-4">
        <Skeleton className="h-10 w-10 rounded-xl" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-7 w-56" />
          <Skeleton className="h-4 w-96 max-w-full" />
          <Skeleton className="h-3 w-72 max-w-full" />
        </div>
        <Skeleton className="h-8 w-24" />
      </div>

      <Skeleton className="h-10 w-full" />

      {/* Health lede beside the reads column */}
      <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-12">
        <div className="space-y-3">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-12 w-40" />
          <Skeleton className="h-16 w-full max-w-[54ch]" />
          <Skeleton className="h-4 w-36" />
        </div>
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-px sm:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-14" />
        ))}
      </div>

      <Skeleton className="h-48" />
      <Skeleton className="h-40" />
    </div>
  );
}
