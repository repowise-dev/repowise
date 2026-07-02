import { getRefactoringTargets } from "@repowise-dev/api-client/refactoring";
import type { RefactoringPlan } from "@repowise-dev/ui/refactoring/types";
import type { RepowiseContext } from "./context";

/**
 * Per-file refactoring-plan fetcher shared by the CodeLens provider and any
 * other feature that needs the deterministic plans for one file. Uses the
 * server-side `filePath` filter so the response is already scoped to the file;
 * caches under the head-commit tag and dedupes concurrent requests for the same
 * (repo, commit, file) so several editors opening the same file issue one call.
 */

const inFlight = new Map<string, Promise<RefactoringPlan[]>>();

/**
 * Plans for `relPath` (repo-relative, forward slashes). Returns [] when no repo
 * is resolved or the request fails; never throws, so a lens provider can await
 * it inline. Results are cached per head commit; a reindex changes the tag and
 * the stale entry is simply never read again.
 */
export async function getPlansForFile(
  ctx: RepowiseContext,
  relPath: string,
): Promise<RefactoringPlan[]> {
  const repoId = ctx.repoId;
  if (!repoId) return [];

  const tag = ctx.repo?.head_commit ?? "";
  const key = `plans:file:${relPath}`;

  const cached = ctx.cache.get<RefactoringPlan[]>(repoId, key, tag);
  if (cached) return cached;

  const dedupeKey = `${repoId} ${tag} ${key}`;
  const existing = inFlight.get(dedupeKey);
  if (existing) return existing;

  const request = getRefactoringTargets(repoId, { filePath: relPath })
    .then((targets) => {
      const plans = targets.plans ?? [];
      ctx.cache.set(repoId, key, tag, plans);
      return plans;
    })
    .catch((err) => {
      ctx.log.debug(`getPlansForFile(${relPath}) failed: ${String(err)}`);
      return [];
    })
    .finally(() => {
      inFlight.delete(dedupeKey);
    });

  inFlight.set(dedupeKey, request);
  return request;
}
