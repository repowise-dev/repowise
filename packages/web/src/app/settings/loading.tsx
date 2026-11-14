import { getTranslations } from "next-intl/server";
import { Skeleton, SkeletonRegion } from "@repowise-dev/ui/ui/skeleton";

/** One group's rows loading; the frame and rail stay put. */
export default async function SettingsLoading() {
  const t = await getTranslations("loading");
  return (
    <SkeletonRegion label={t("settings")} className="flex flex-col gap-3">
      <Skeleton className="h-5 w-40" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
    </SkeletonRegion>
  );
}
