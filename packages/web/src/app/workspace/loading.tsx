import { getTranslations } from "next-intl/server";
import { PageSkeleton, StatGridSkeleton } from "@repowise-dev/ui/shared/loading-skeletons";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

/** Workspace overview: header, a stat ribbon, then the repository list. */
export default async function WorkspaceLoading() {
  const t = await getTranslations("loading");
  return (
    <PageSkeleton label={t("workspace")}>
      <StatGridSkeleton count={4} />
      <Skeleton className="h-64 w-full" />
    </PageSkeleton>
  );
}
