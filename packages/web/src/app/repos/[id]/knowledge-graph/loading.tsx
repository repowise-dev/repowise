import { getTranslations } from "next-intl/server";
import { PageSkeleton } from "@repowise-dev/ui/shared/loading-skeletons";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * Mirrors the Knowledge Graph layout. `maxWidth` and the 520px canvas box
 * both match the page: the page's own in-flight state renders a centred
 * `h-[520px]` band, so this hands over to it without the canvas changing
 * size.
 */
export default async function KnowledgeGraphLoading() {
  const t = await getTranslations("loading");
  return (
    <PageSkeleton maxWidth="wide" label={t("knowledgeGraph")}>
      <Skeleton className="h-[520px] w-full rounded-lg" />
    </PageSkeleton>
  );
}
