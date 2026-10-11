import { getTranslations } from "next-intl/server";
import { PageSkeleton } from "@repowise-dev/ui/shared/loading-skeletons";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

export default async function SettingsLoading() {
  const t = await getTranslations("loading");
  return (
    <PageSkeleton maxWidth="narrow" actions={false} label={t("settings")}>
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-40 w-full" />
    </PageSkeleton>
  );
}
