"use client";

import { useQueryState } from "nuqs";
import type { CommitResponse, CommitStats } from "@/lib/api/types";
import { CommitDistribution as CommitDistributionView } from "@repowise-dev/ui/commits/commit-distribution";

/**
 * The app's wiring for the shared distribution charts: everything this file
 * still owns is the decision that "open this commit" means writing `?commit=`
 * for the detail sheet to read. The headings, the prose and the charts moved to
 * `@repowise-dev/ui/commits` so a second surface cannot end up with its own
 * copy of the explanation.
 *
 * A client island only because of that query-param write.
 */
export function CommitDistribution({
  stats,
  recent,
}: {
  stats: CommitStats | null;
  recent: CommitResponse[];
}) {
  const [, setSelectedSha] = useQueryState("commit");

  return (
    <CommitDistributionView
      stats={stats}
      recent={recent}
      onSelect={(sha) => void setSelectedSha(sha)}
    />
  );
}
