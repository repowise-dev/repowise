import type { ElementType } from "react";
import { ArrowRight, GitPullRequestArrow } from "lucide-react";
import type { ChangeCandidate, ChangeCandidatesResponse } from "@repowise-dev/types/blast-radius";
import { cn } from "../lib/cn";
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
  // Same row shape as the Overview's repo report line, without its accent.
  return (
    <p className="m-0 flex min-h-10 flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-l-2 border-[var(--color-border-default)] px-3 py-2 text-[13px] text-[var(--color-text-secondary)]">
      <GitPullRequestArrow className="size-3.5 shrink-0 text-[var(--color-text-tertiary)]" aria-hidden />
      <span>Your change:</span>
      <span
        className={cn(
          "min-w-0 truncate font-medium text-[var(--color-text-primary)]",
          change.kind === "branch" && "font-mono",
        )}
      >
        {name}
      </span>
      {files != null && <span className="tabular-nums">· {files} file{files === 1 ? "" : "s"}</span>}
      <Link
        href={href(changeLinkParam(change.id))}
        aria-label={`Review ${name}`}
        className="ml-auto inline-flex items-center gap-1 rounded-sm font-medium text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        Review
        <ArrowRight aria-hidden className="size-3.5" />
      </Link>
    </p>
  );
}
