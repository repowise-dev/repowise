"use client";

/**
 * Dead Code host — binds the shared {@link DeadCodeView} to web's `/api`
 * client and `/repos/:id` routing. The composition (safe-to-delete pile,
 * cluster rollups, drill-down table, patch/undo, bulk resolve, Propose
 * cleanup, Re-analyze) lives in `@repowise-dev/ui/dead-code`; this file only
 * injects the app-specific pieces so web and hosted render the same view.
 */

import { useRouter } from "next/navigation";
import { DeadCodeView, type DeadCodeAdapter } from "@repowise-dev/ui/dead-code";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getDeadCodeSummary,
  listDeadCode,
  analyzeDeadCode,
  patchDeadCodeFinding,
} from "@/lib/api/dead-code";

export function DeadCodeTab({ repoId }: { repoId: string }) {
  const router = useRouter();
  const prefix = `/repos/${repoId}`;

  const adapter: DeadCodeAdapter = {
    cacheKey: repoId,
    repoId,
    getSummary: () => getDeadCodeSummary(repoId),
    listFindings: (opts) => listDeadCode(repoId, opts),
    analyze: async () => {
      await analyzeDeadCode(repoId);
    },
    patchFinding: (findingId, patch) => patchDeadCodeFinding(findingId, patch),
    fileHref: (path) => fileEntityPath(prefix, path),
    navigate: (href) => router.push(href),
  };

  return <DeadCodeView adapter={adapter} />;
}
