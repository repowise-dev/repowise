import type { NextAction } from "@repowise-dev/types/actions";

import { attentionSourceHref } from "../dashboard/attention-href";
import { fileEntityPath } from "../shared/entity/routes";

/**
 * Where an action's evidence lives, relative to the repo link prefix.
 *
 * A plain module, not beside the client component that renders the rows, so a
 * server page can call it too (see the note on `getDefaultHref`).
 */
export function actionHref(action: NextAction, prefix: string): string {
  const { path } = action.target;
  switch (action.surface) {
    case "file":
      return path ? fileEntityPath(prefix, path) : `${prefix}/code-health?tab=findings`;
    case "findings":
      return action.target.kind === "file" || action.target.kind === "symbol"
        ? fileEntityPath(prefix, path)
        : `${prefix}/code-health?tab=findings`;
    case "performance":
      return action.evidence_ids[0]
        ? `${prefix}/code-health?tab=performance&opportunity=${encodeURIComponent(action.evidence_ids[0])}`
        : `${prefix}/code-health?tab=performance`;
    case "coverage":
      return `${prefix}/code-health?tab=coverage`;
    case "commits":
      return `${prefix}/commits`;
    case "decisions":
      return action.target.kind === "decision" && path
        ? `${prefix}/decisions/${encodeURIComponent(path)}`
        : `${prefix}/decisions`;
    case "security":
      return attentionSourceHref("security", prefix);
    case "doc_drift":
      return attentionSourceHref("doc_drift", prefix);
    case "dead_code":
      return attentionSourceHref("dead_code", prefix);
    default:
      return prefix;
  }
}
