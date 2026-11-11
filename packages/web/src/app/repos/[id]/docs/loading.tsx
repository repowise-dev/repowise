import { getTranslations } from "next-intl/server";
import { SkeletonRegion, Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { DOCS_READER_SHELL_CLASS } from "./docs-reader-shell";

/** Reader-shaped: the docs header band, the page tree, then the article. */
export default async function DocsLoading() {
  const t = await getTranslations("loading");
  return (
    <SkeletonRegion className={DOCS_READER_SHELL_CLASS} label={t("docs")}>
      <div className="shrink-0 border-b border-[var(--color-border-default)] px-4 py-3 sm:px-6">
        <Skeleton className="h-6 w-56 max-w-full" />
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="hidden w-64 shrink-0 space-y-2 border-r border-[var(--color-border-default)] p-4 md:block">
          {Array.from({ length: 9 }).map((_, i) => (
            <Skeleton key={i} className="h-4 w-full" />
          ))}
        </div>
        <div className="min-w-0 flex-1 space-y-4 p-4 sm:p-6">
          <Skeleton className="h-8 w-2/3 max-w-lg" />
          <Skeleton className="h-4 w-full max-w-[68ch]" />
          <Skeleton className="h-4 w-5/6 max-w-[68ch]" />
          <Skeleton className="h-4 w-3/4 max-w-[68ch]" />
          <Skeleton className="h-48 w-full max-w-[68ch]" />
        </div>
      </div>
    </SkeletonRegion>
  );
}
