import { PageSkeleton } from "@repowise-dev/ui/shared/loading-skeletons";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

/**
 * Mirrors the Knowledge Graph layout. `maxWidth` and the 520px canvas box
 * both match the page: the page's own in-flight state renders a centred
 * `h-[520px]` band, so this hands over to it without the canvas changing
 * size.
 */
export default function KnowledgeGraphLoading() {
  return (
    <PageSkeleton maxWidth="wide" label="Loading knowledge graph">
      <Skeleton className="h-[520px] w-full rounded-lg" />
    </PageSkeleton>
  );
}
