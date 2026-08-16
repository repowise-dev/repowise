import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * Shapes match the real header: eyebrow, path, the lede's 44px figure beside
 * its prose column, the hairline stat ribbon, then the tab row and one panel.
 * The old version drew a row of pills the page no longer has and no figure at
 * all, so content landing reflowed everything — which reads as slower than
 * showing nothing.
 */
export default function FilePageLoading() {
  return (
    <div className="mx-auto flex w-full max-w-[1280px] flex-col p-[var(--page-pad)]">
      <div className="flex flex-col gap-8">
        <div className="flex flex-col gap-6">
          <div>
            <Skeleton className="h-3 w-8" />
            <Skeleton className="mt-2 h-7 w-2/3" />
          </div>
          <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:gap-12">
            <div className="lg:w-[220px]">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="mt-2.5 h-11 w-28" />
            </div>
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-full max-w-[62ch]" />
              <Skeleton className="h-4 w-5/6 max-w-[62ch]" />
              <Skeleton className="h-4 w-3/4 max-w-[62ch]" />
            </div>
          </div>
          <Skeleton className="h-[74px] w-full" />
        </div>
        <Skeleton className="h-9 w-full max-w-lg" />
        <Skeleton className="h-96 w-full" />
      </div>
    </div>
  );
}
