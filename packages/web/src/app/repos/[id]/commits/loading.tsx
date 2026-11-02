import { getTranslations } from "next-intl/server";
import { PageSkeleton, TableSkeleton } from "@repowise-dev/ui/shared/loading-skeletons";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * Mirrors the Commits layout: the shell header, the lede stats, the
 * evolution chart section, then the commit table.
 */
export default async function CommitsLoading() {
  const t = await getTranslations("loading");
  return (
    <PageSkeleton actions={false} label={t("commits")}>
      <Skeleton className="h-28 w-full rounded-xl" />
      <div className="space-y-3">
        <Skeleton className="h-5 w-64" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
      <TableSkeleton rows={8} />
    </PageSkeleton>
  );
}
