import { GitCommit as GitCommitIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { EmptyState } from "../shared/empty-state";
import { formatRelativeTime } from "../lib/format";

export interface CommitsMiniEntry {
  sha: string;
  short_sha: string;
  subject: string;
  author_name: string;
  committed_at: string | null;
  change_risk_level?: string | null;
  risk_percentile?: number | null;
}

interface CommitsMiniProps {
  commits: CommitsMiniEntry[];
  repoId: string;
  linkPrefix?: string;
  previewCount?: number;
}

const RISK_DOT: Record<string, string> = {
  high: "bg-[var(--color-error)]",
  moderate: "bg-[var(--color-caution)]",
  low: "bg-[var(--color-success)]",
};

/** Recent-commits feed for the Overview Pulse tab — real activity, linked
 *  through to the commits page (`?commit=` opens the detail sheet). */
export function CommitsMini({ commits, repoId, linkPrefix, previewCount = 8 }: CommitsMiniProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const shown = commits.slice(0, previewCount);

  if (shown.length === 0) {
    return (
      <Card>
        <CardContent className="p-0">
          <EmptyState
            icon={<GitCommitIcon className="h-8 w-8" />}
            title="No commit history"
            description="Commit activity appears once git history is indexed."
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <GitCommitIcon className="h-4 w-4 text-[var(--color-text-secondary)]" />
            Recent Commits
          </span>
          <a
            href={`${prefix}/commits`}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline font-normal"
          >
            View all
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="space-y-1.5">
          {shown.map((c) => (
            <a
              key={c.sha}
              href={`${prefix}/commits?commit=${c.sha}`}
              className="flex items-center gap-2.5 -mx-2 px-2 py-1 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
            >
              <span
                className={`h-2 w-2 rounded-full shrink-0 ${
                  RISK_DOT[c.change_risk_level ?? ""] ?? "bg-[var(--color-text-tertiary)]"
                }`}
                title={c.change_risk_level ? `${c.change_risk_level} change risk` : undefined}
              />
              <span className="text-xs font-mono text-[var(--color-text-tertiary)] shrink-0">
                {c.short_sha}
              </span>
              <span className="text-xs text-[var(--color-text-primary)] truncate flex-1 min-w-0">
                {c.subject}
              </span>
              <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0 hidden sm:inline">
                {c.author_name}
              </span>
              <span className="text-[10px] text-[var(--color-text-tertiary)] tabular-nums shrink-0">
                {c.committed_at ? formatRelativeTime(c.committed_at) : ""}
              </span>
            </a>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
