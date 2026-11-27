/**
 * Change Risk panel: the web Review page. The shared impact view opens the
 * most relevant change (work in progress, else the checked-out branch) and
 * reads it the way the web does: findings and what to check first, the
 * change-risk rank demoted to one line beside its review priority, then where
 * the change reaches, folded.
 *
 * Mirrors web's `components/risk/impact-tab.tsx`: the same adapter over the
 * same endpoints, through the host. The one difference is links: a file opens
 * in an editor column, and the Markdown review link is left out because a
 * webview cannot open the server's raw route (the agent prompt carries it).
 */

import { useMemo } from "react";
import { GitPullRequestArrow } from "lucide-react";
import { ImpactView, type ImpactAdapter } from "@repowise-dev/ui/blast-radius";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import type { ViewProps } from "../../runtime/mount";
import { fileHref, useFileLinks } from "../../runtime/fileLinks";

/** The web page's English strings (`views.review`). */
const COPY = {
  title: "Review",
  description:
    "Pick a change, uncommitted work, a branch or a commit, to see what it reaches, which files to look at first, which tests to run and who to ask.",
} as const;

export function App({ host, repo, refreshToken }: ViewProps<"risk">) {
  useFileLinks(host);
  const adapter: ImpactAdapter = useMemo(
    () => ({
      // The view's SWR keys: a refresh (the index moved) reads everything again.
      cacheKey: `${repo.id}:${refreshToken}`,
      listHotspots: (limit) => host.api.hotspots(limit),
      searchFiles: async (q) => (await host.api.searchNodes(q, 8)).map((r) => r.node_id),
      analyze: ({ changedFiles, maxDepth }) =>
        host.api.blastRadius({ changed_files: changedFiles, max_depth: maxDepth, with_graph: true }),
      listChanges: () => host.api.reviewChanges(),
      analyzeChange: ({ changeId, maxDepth, offset, limit, withGraph }) =>
        host.api.changeBlastRadius({
          change_id: changeId,
          max_depth: maxDepth,
          ...(offset != null ? { offset } : {}),
          ...(limit != null ? { limit } : {}),
          ...(withGraph ? { with_graph: true } : {}),
        }),
      checkHealth: (changeId) => host.api.changeHealthCheck(changeId),
      fileHref: (path) => fileHref(path),
    }),
    [host, repo.id, refreshToken],
  );

  return (
    <PageShell icon={<GitPullRequestArrow />} title={COPY.title} description={COPY.description}>
      {/* Remounted when the index moves, so an open review is read again. */}
      <ImpactView key={`${repo.id}:${refreshToken}`} adapter={adapter} />
    </PageShell>
  );
}
