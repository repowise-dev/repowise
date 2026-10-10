import { getTranslations } from "next-intl/server";
import { PageSkeleton } from "@repowise-dev/ui/shared/loading-skeletons";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * App-shell fallback for the routes outside a repo (home, settings, the
 * workspace pages). Every one of them is a `PageShell`, so the frame is
 * exact; the body is one reserved block rather than a guessed composition.
 */
export default async function AppLoading() {
  const t = await getTranslations("loading");
  return (
    <PageSkeleton label={t("page")}>
      <Skeleton className="h-[60vh] min-h-80 w-full rounded-xl" />
    </PageSkeleton>
  );
}
