import { FileQuestion } from "lucide-react";
import type { DocDriftUnavailable } from "@repowise-dev/types/doc-drift";

import { EmptyState } from "../shared/empty-state";

/**
 * The three refusals, rendered here rather than pre-gated by each host.
 *
 * This is the state that matters most on this surface and the one most easily
 * lost. A drift pass that never ran produces an empty findings list, which is
 * indistinguishable from a repository whose documentation is correct — so the
 * engine returns a cause instead of an empty list, and a host that gates on
 * the cause itself grows its own wording for it. Health did exactly that and
 * hosted ended up with a forked "unscored" component.
 *
 * Each cause names something a reader can act on. "Reindex" in particular is
 * wrong advice for a locked database, which is why `drift_read_failed` is a
 * separate cause and not folded into the stale-index one.
 */
type Copy = { title: string; description: string; retryable: boolean };

const COPY: Record<DocDriftUnavailable, Copy> = {
  not_computed: {
    title: "Documentation has not been checked yet",
    description:
      "This index carries no drift results, so the pass has not run on it. It runs as part of an update. Until then this view cannot tell a repository with correct documentation from one nobody has looked at, and it will not guess.",
    retryable: false,
  },
  index_predates_doc_drift: {
    title: "This index predates documentation drift",
    description:
      "The index was built before this check existed, so there is nothing to read. Re-index the repository to populate it.",
    retryable: false,
  },
  drift_read_failed: {
    title: "Could not read the drift results",
    description:
      "The index is there but the read failed, which usually means the database is locked by another process rather than that anything is missing. Re-indexing will not help; try again once the other process has finished.",
    retryable: true,
  },
};

/**
 * For a cause this build does not know. The engine owns the vocabulary and can
 * grow it, and a hard lookup would take the whole tab down with an
 * `undefined.title` rather than degrade. Retryable, because an unrecognised
 * cause is not evidence that retrying is pointless.
 */
const UNKNOWN: Copy = {
  title: "Documentation drift is unavailable",
  description:
    "The index could not answer, and gave a reason this version does not recognise. Updating Repowise may explain it.",
  retryable: true,
};

export function DocDriftUnavailableState({
  reason,
  titleAs,
  onRetry,
}: {
  reason: DocDriftUnavailable;
  titleAs?: "h2" | "h3";
  /** Offered only where retrying could change the answer. */
  onRetry?: (() => void) | undefined;
}) {
  const copy = COPY[reason] ?? UNKNOWN;
  return (
    <EmptyState
      icon={<FileQuestion className="h-6 w-6" />}
      title={copy.title}
      description={copy.description}
      {...(titleAs ? { titleAs } : {})}
      {...(copy.retryable && onRetry
        ? { action: { label: "Try again", onClick: onRetry } }
        : {})}
    />
  );
}
