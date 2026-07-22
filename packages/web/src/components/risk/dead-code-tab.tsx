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
import { getJob } from "@/lib/api/jobs";

const POLL_INTERVAL_MS = 3_000;
/** Give up watching after this long; the job may still be running server-side. */
const POLL_TIMEOUT_MS = 15 * 60_000;

/**
 * Resolve once the analysis job finishes. Polling rather than SSE: the view
 * only needs the terminal state to know when to refetch, and a plain promise
 * keeps the shared view free of any streaming dependency.
 */
async function waitForJob(jobId: string): Promise<void> {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  for (;;) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    const job = await getJob(jobId);
    if (job.status === "completed") return;
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.error_message || `Analysis ${job.status}`);
    }
    if (Date.now() > deadline) {
      throw new Error("Analysis is taking longer than expected. Reload to see the results.");
    }
  }
}

export function DeadCodeTab({ repoId }: { repoId: string }) {
  const router = useRouter();
  const prefix = `/repos/${repoId}`;

  const adapter: DeadCodeAdapter = {
    cacheKey: repoId,
    repoId,
    getSummary: () => getDeadCodeSummary(repoId),
    listFindings: (opts) => listDeadCode(repoId, opts),
    analyze: () => analyzeDeadCode(repoId),
    waitForAnalysis: waitForJob,
    patchFinding: (findingId, patch) => patchDeadCodeFinding(findingId, patch),
    fileHref: (path) => fileEntityPath(prefix, path),
    navigate: (href) => router.push(href),
  };

  return <DeadCodeView adapter={adapter} />;
}
