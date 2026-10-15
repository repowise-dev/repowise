"use client";

/**
 * CodeGraphTreeView — the Architecture tab that draws the dependency graph as
 * an outline instead of a canvas.
 *
 * Data comes from the same `/api/graph/{repo_id}` payload the Map tab's file
 * scope draws (`useGraph`), under the same SWR key, so moving between the two
 * tabs is a re-render rather than a second fetch, and a revalidation on one is
 * a revalidation on both. Nothing here resolves imports or builds a graph: the
 * server already ships each edge's `imported_names`.
 *
 * The node cap is real, so it is stated. `/api/graph` returns the most
 * connected slice of a large repo with `truncated=true`; an outline that showed
 * 1,500 of 3,194 files without saying so would read as the whole repository.
 * Same banner and same stepped "load more" as the canvas, for that reason.
 */

import { useCallback, useState } from "react";
import Link from "next/link";
import { ListTree } from "lucide-react";
import { CodeGraphTree } from "@repowise-dev/ui/graph/code-graph-tree";
import { GraphTruncationBanner } from "@repowise-dev/ui/graph/graph-truncation-banner";
import {
  GraphScopeSwitcher,
  type GraphScope,
} from "@repowise-dev/ui/graph/graph-scope-controls";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useGraph } from "@/lib/hooks/use-graph";

export function CodeGraphTreeView({
  repoId,
  scope,
  onScopeChange,
}: {
  repoId: string;
  /** Controlled by the page, which owns `?view=`, so the one scope control on
   *  this axis carries the way back to the canvas. */
  scope: GraphScope;
  onScopeChange: (scope: GraphScope) => void;
}) {
  const [graphLimit, setGraphLimit] = useState<number | undefined>(undefined);
  const { graph, error, isLoading, mutate } = useGraph(repoId, graphLimit);

  const fileHref = useCallback(
    (path: string) => fileEntityPath(`/repos/${repoId}`, path),
    [repoId],
  );

  if (error && !graph) {
    return (
      <ApiError
        title="Couldn't load the code graph"
        message={toFriendlyMessage(error)}
        onRetry={() => void mutate()}
      />
    );
  }

  return (
    <div className="max-w-[1600px] space-y-5 p-4 sm:p-6">
      <div className="flex flex-col gap-3 border-b border-[var(--color-border-default)] pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="mb-1 flex items-center gap-2 text-xl font-semibold text-[var(--color-text-primary)]">
            <ListTree className="h-5 w-5 text-[var(--color-accent-primary)]" />
            Code graph tree
          </h1>
          <p className="max-w-3xl text-sm text-[var(--color-text-secondary)]">
            The same files and dependencies the Map draws, as a tree you can open. Expand a
            directory to walk it, then a file to see the types it imports and which file each
            one comes from. Counts are read off this payload, so the tree and the map never
            disagree about how many files are in it.
          </p>
        </div>
        <div className="shrink-0">
          <GraphScopeSwitcher scope={scope} onScopeChange={onScopeChange} />
        </div>
      </div>

      {graph?.truncated && graph.total_node_count != null && (
        <GraphTruncationBanner
          shown={graph.nodes.length}
          total={graph.total_node_count}
          limit={graphLimit ?? graph.nodes.length}
          onLoadMore={(nextLimit) => setGraphLimit(nextLimit)}
        />
      )}

      <CodeGraphTree
        graph={graph}
        isLoading={isLoading}
        fileHref={fileHref}
        LinkComponent={Link}
      />
    </div>
  );
}
