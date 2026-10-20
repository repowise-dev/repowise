import { SkeletonRegion, Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { getTranslations } from "next-intl/server";
import { repoNavGroups } from "./nav-items";

/**
 * The repo nav's shape, read from the same source the sidebar renders: one
 * entry per group, its item count, and whether it carries a label row. The
 * repo id is irrelevant to the shape, so a placeholder stands in.
 */
const NAV_SHAPE = repoNavGroups("_").map((g) => ({
  id: g.id,
  labelled: Boolean(g.label),
  rows: g.items.length,
}));

/**
 * The shell itself, waiting. This is the one place with no page layout to
 * mirror, because the shell is what has not painted yet: it stands in for the
 * sidebar rail and the content column, at the sidebar's own expanded width.
 *
 * It replaces `fallback={null}`, which showed nothing at all while the shell
 * suspended, so a slow first paint looked like a blank app rather than a
 * loading one.
 */
export async function AppShellSkeleton() {
  const t = await getTranslations("shell");

  return (
    <SkeletonRegion
      className="flex h-screen overflow-hidden"
      label={t("skeleton.loading")}
    >
      {/* Matches Sidebar's expanded width (`w-[280px]`). */}
      <div className="hidden w-[280px] shrink-0 flex-col gap-2 border-r border-[var(--color-border-default)] p-4 md:flex">
        <Skeleton className="h-8 w-32" />
        <div className="mt-4 space-y-1.5">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
        {/* The active repository, expanded: its row, then each nav group. */}
        <Skeleton className="mt-4 h-9 w-full" />
        <div className="ml-3.5 space-y-1.5 pl-3">
          {NAV_SHAPE.map((g) => (
            <div key={g.id} className="space-y-1.5 pt-1.5">
              {g.labelled && <Skeleton className="h-3 w-24" />}
              {Array.from({ length: g.rows }).map((_, i) => (
                <Skeleton key={i} className="h-7 w-full" />
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="flex-1 p-[var(--page-pad)]">
        <Skeleton className="h-full w-full rounded-xl" />
      </div>
    </SkeletonRegion>
  );
}
