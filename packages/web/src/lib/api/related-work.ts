import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import type { RelatedWorkHref } from "@repowise-dev/ui/health";
import { refactoringOpportunityHref } from "./file-opportunity";

export { getRelatedWork } from "./code-health";

/**
 * Where one related item lives in this app. Each lens opens on its own surface:
 * a refactoring plan or performance cause in its drawer, a finding on the
 * file's health tab, Fix first on the Code Health overview that leads with it,
 * dead code on its tab (it has no per-item drawer to open). Every id in a URL
 * is encoded, here or by the helper that builds it.
 */
export function relatedWorkHref(repoId: string): RelatedWorkHref {
  const prefix = `/repos/${repoId}`;
  return (item, filePath) => {
    switch (item.lens) {
      case "refactoring":
        return refactoringOpportunityHref(repoId, item.id);
      case "performance":
        return `${prefix}/code-health?tab=performance&opportunity=${encodeURIComponent(item.id)}`;
      case "findings":
        return `${fileEntityPath(prefix, filePath)}?tab=health`;
      case "fix_first":
        return `${prefix}/code-health`;
      case "dead_code":
        return `${prefix}/code-health?tab=dead-code`;
    }
  };
}
