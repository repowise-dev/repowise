import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import type { RelatedWorkItem } from "@repowise-dev/types/health";
import { refactoringOpportunityHref } from "./file-opportunity";

export { getRelatedWork } from "./code-health";

/**
 * Where one related item lives in this app. Each lens opens on its own surface:
 * a refactoring plan or performance cause in its drawer, a finding on the
 * file's health tab, Fix first on the Code Health overview that leads with it,
 * dead code on its tab (it has no per-item drawer to open).
 */
export function relatedWorkHref(repoId: string, filePath: string, item: RelatedWorkItem): string {
  const prefix = `/repos/${repoId}`;
  const id = encodeURIComponent(item.id);
  switch (item.lens) {
    case "refactoring":
      return refactoringOpportunityHref(repoId, item.id);
    case "performance":
      return `${prefix}/code-health?tab=performance&opportunity=${id}`;
    case "findings":
      return `${fileEntityPath(prefix, filePath)}?tab=health`;
    case "fix_first":
      return `${prefix}/code-health`;
    case "dead_code":
      return `${prefix}/code-health?tab=dead-code`;
  }
}
