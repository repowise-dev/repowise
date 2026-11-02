import { getTranslations } from "next-intl/server";
import { PageFrame } from "@repowise-dev/ui/shared/page-shell";
import { Skeleton, SkeletonRegion } from "@repowise-dev/ui/ui/skeleton";

export default async function SymbolPageLoading() {
  const t = await getTranslations("loading");
  return (
    <SkeletonRegion label={t("symbol")}>
      <PageFrame>
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-20 w-full" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      </PageFrame>
    </SkeletonRegion>
  );
}
