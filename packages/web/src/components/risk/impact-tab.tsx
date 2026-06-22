"use client";

/**
 * Impact host — binds the shared {@link ImpactView} to web's `/api` client and
 * supplies the reviewer-suggestions panel as the analyzer's reviewer slot. The
 * composition (file picker, hotspot chips, depth control, blast-radius results)
 * lives in `@repowise-dev/ui/blast-radius`; this file only injects the
 * app-specific pieces so web and hosted render the same view.
 */

import useSWR from "swr";
import { ImpactView, type ImpactAdapter } from "@repowise-dev/ui/blast-radius";
import { ReviewerSuggestions } from "@repowise-dev/ui/git/reviewer-suggestions";
import { analyzeBlastRadius } from "@/lib/api/blast-radius";
import { getHotspots, getReviewerSuggestions } from "@/lib/api/git";
import { searchNodes } from "@/lib/api/graph";

/**
 * Reviewer slot — fetches suggestions for the analyzed changeset and renders
 * the rich panel, or nothing when there are none (graceful degradation).
 */
function ReviewersPanel({ repoId, files }: { repoId: string; files: string[] }) {
  const { data } = useSWR(
    files.length ? ["impact-reviewers", repoId, files.join("\n")] : null,
    () =>
      getReviewerSuggestions(repoId, files, 8)
        .then((r) => r.suggestions)
        .catch(() => []),
    { revalidateOnFocus: false },
  );
  if (!data || data.length === 0) return null;
  return (
    <ReviewerSuggestions
      suggestions={data}
      subtitle={`Based on authorship and co-change history for ${files.length} changed paths`}
    />
  );
}

export function ImpactTab({ repoId }: { repoId: string }) {
  const adapter: ImpactAdapter = {
    cacheKey: repoId,
    listHotspots: (limit) => getHotspots(repoId, limit),
    searchFiles: async (q) => {
      const results = await searchNodes(repoId, q, 8);
      return results.map((r) => r.node_id);
    },
    analyze: ({ changedFiles, maxDepth }) =>
      analyzeBlastRadius(repoId, { changed_files: changedFiles, max_depth: maxDepth }),
    renderReviewers: (files) => <ReviewersPanel repoId={repoId} files={files} />,
  };

  return <ImpactView adapter={adapter} />;
}
