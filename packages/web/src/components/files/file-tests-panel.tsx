"use client";

/**
 * The Tests tab's graph-inferred test list, bound to web's `/api` client.
 *
 * Client-side for the same reason `FileHealthPanel` is: `TestsReachingList`
 * fetches, and a fetcher cannot be handed to a server-rendered tab body as a
 * prop. The server page composes this element and passes it down as a node.
 */

import { TestsReachingList } from "@repowise-dev/ui/health";
import { getTestsReaching } from "@/lib/api/code-health";

export function FileTestsPanel({
  repoId,
  filePath,
}: {
  repoId: string;
  filePath: string;
}) {
  return (
    <TestsReachingList
      filePath={filePath}
      cacheKey={repoId}
      fetcher={(p) => getTestsReaching(repoId, p)}
    />
  );
}
