import { getTranslations } from "next-intl/server";
import { PageSkeleton } from "@repowise-dev/ui/shared/loading-skeletons";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * The route's first paint, before the page's own wait (`ZoomCanvasSkeleton`,
 * the canvas frame with faint cards) takes over. `maxWidth` matches the page.
 */
export default async function KnowledgeGraphLoading() {
  const t = await getTranslations("loading");
  return (
    <PageSkeleton maxWidth="wide" label={t("knowledgeGraph")}>
      <Skeleton className="h-[520px] w-full rounded-lg" />
    </PageSkeleton>
  );
}
