import { getRefactoringOpportunities } from "@repowise-dev/api-client/refactoring";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";

/**
 * The one composed refactoring opportunity for a file, or null.
 *
 * "One" is a measured fact, not an assumption the caller has to defend: on the
 * dogfood index there are 581 open opportunities over 581 distinct files,
 * because an opportunity *is* a file's plans folded together. The endpoint still
 * returns a page, so this takes the first row and lets the shape stay honest if
 * that ever stops being true.
 *
 * Steps are requested because the finding link only appears where a step
 * actually addresses that finding's cause, and the maximum step count on a real
 * index is 11.
 */
export async function getFileOpportunity(
  repoId: string,
  filePath: string,
): Promise<RefactoringOpportunity | null> {
  const page = await getRefactoringOpportunities(repoId, {
    filePath,
    stepPreview: 20,
    limit: 1,
  });
  return page.items[0] ?? null;
}

/** Deep link into the refactoring surface for one opportunity. */
export function refactoringOpportunityHref(repoId: string, opportunityId: string): string {
  return `/repos/${repoId}/refactoring?opportunity=${encodeURIComponent(opportunityId)}`;
}
