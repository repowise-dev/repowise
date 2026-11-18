import type { ElementType } from "react";
import { GitPullRequestArrow } from "lucide-react";
import type { ChangeCandidate, ChangeCandidatesResponse } from "@repowise-dev/types/blast-radius";
import { changeLinkParam } from "./change-links";

/**
 * The change worth a review right now: uncommitted work, else the checked-out
 * branch when it is ahead of the default branch. Nothing else counts as
 * "your change"; an older branch or a merged commit is history.
 */
export function currentChange(data: ChangeCandidatesResponse | null | undefined): ChangeCandidate | null {
  if (!data) return null;
  return (
    data.changes.find((c) => c.kind === "working_tree") ??
    data.changes.find((c) => c.kind === "branch" && c.is_current) ??
    null
  );
}

/**
 * One compact line pointing at the change in progress: what it is, how many
 * files, and a link into Review. Renders nothing when there is nothing in
 * progress. Reads only the candidate list, never runs the analysis.
 */
export function ReviewNudge({
  data,
  href,
  LinkComponent = "a",
}: {
  data: ChangeCandidatesResponse | null | undefined;
  /** Where Review opens for a change link value (`wip`, a branch name, or `branch:<name>` for one named like those). */
  href: (change: string) => string;
  LinkComponent?: ElementType;
}) {
  const change = currentChange(data);
  if (!change) return null;
  const Link = LinkComponent;
  const name = change.kind === "working_tree" ? "Uncommitted changes" : change.label;
  const files = change.files_changed;
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-[var(--color-text-secondary)]">
      <GitPullRequestArrow className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" aria-hidden />
      <span>Your change:</span>
      <span className={change.kind === "branch" ? "font-mono text-[var(--color-text-primary)]" : "text-[var(--color-text-primary)]"}>
        {name}
      </span>
      {files != null && <span className="tabular-nums">· {files} file{files === 1 ? "" : "s"}</span>}
      <Link
        href={href(changeLinkParam(change.id))}
        className="font-medium text-[var(--color-accent-primary)] hover:underline"
      >
        Review →
      </Link>
    </p>
  );
}
