import { GitCommit, User } from "lucide-react";
import { Badge } from "@repowise/ui/ui/badge";
import { CommitCategorySparkline } from "@/components/git/commit-category-sparkline";
import { CoChangeList } from "@/components/git/co-change-list";
import { formatRelativeTime, formatDate, formatAgeDays } from "@/lib/utils/format";
import type { GitMetadataResponse } from "@/lib/api/types";

interface GitHistoryPanelProps {
  git: GitMetadataResponse;
}

const AUTHOR_COLORS = [
  "bg-blue-500", "bg-purple-500", "bg-green-500", "bg-yellow-500",
  "bg-pink-500", "bg-indigo-500", "bg-teal-500", "bg-orange-500",
];

const AUTHOR_BAR_COLORS = [
  "#3b82f6", "#a855f7", "#22c55e", "#eab308",
  "#ec4899", "#6366f1", "#14b8a6", "#f97316",
];

function authorColorIndex(name: string): number {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return Math.abs(hash) % AUTHOR_COLORS.length;
}

function AuthorAvatar({ name }: { name: string }) {
  const initials = name
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");

  const color = AUTHOR_COLORS[authorColorIndex(name)];

  return (
    <div
      className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[9px] font-semibold text-white ${color}`}
      aria-label={name}
      title={name}
    >
      {initials || <User className="h-3 w-3" />}
    </div>
  );
}

function getVelocityLabel(git: GitMetadataResponse): { label: string; color: string } {
  if (git.commit_count_90d === 0) return { label: "Inactive", color: "text-[var(--color-text-tertiary)]" };
  const rate30 = git.commit_count_30d / 30;
  const rate90 = git.commit_count_90d / 90;
  if (rate30 > rate90 * 1.2) return { label: "Accelerating", color: "text-red-400" };
  if (rate30 < rate90 * 0.8) return { label: "Cooling", color: "text-green-400" };
  return { label: "Steady", color: "text-[var(--color-text-secondary)]" };
}

export function GitHistoryPanel({ git }: GitHistoryPanelProps) {
  const commits = git.significant_commits ?? [];
  const velocity = getVelocityLabel(git);

  return (
    <div className="space-y-4">
      {/* File lifecycle */}
      <div>
        <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
          File Status
        </p>
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            {git.is_hotspot && <Badge variant="outdated">Hotspot</Badge>}
            {git.is_stable && <Badge variant="fresh">Stable</Badge>}
            {!git.is_hotspot && !git.is_stable && <Badge variant="default">Active</Badge>}
            {git.test_gap === true && <Badge variant="outdated">No tests</Badge>}
            <span className={`text-xs ${velocity.color}`}>{velocity.label}</span>
          </div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <span className="text-[var(--color-text-tertiary)]">Age</span>
            <span className="text-[var(--color-text-secondary)] tabular-nums">
              {formatAgeDays(git.age_days)}
            </span>
            <span className="text-[var(--color-text-tertiary)]">Commits (90d)</span>
            <span className="text-[var(--color-text-secondary)] tabular-nums">
              {git.commit_count_90d}
            </span>
            <span className="text-[var(--color-text-tertiary)]">Churn</span>
            <span className="text-[var(--color-text-secondary)] tabular-nums">
              {Math.round(git.churn_percentile)}%
            </span>
            {git.first_commit_at && (
              <>
                <span className="text-[var(--color-text-tertiary)]">First commit</span>
                <span className="text-[var(--color-text-secondary)]">
                  {formatDate(git.first_commit_at)}
                </span>
              </>
            )}
            {git.last_commit_at && (
              <>
                <span className="text-[var(--color-text-tertiary)]">Last commit</span>
                <span className="text-[var(--color-text-secondary)]">
                  {formatRelativeTime(git.last_commit_at)}
                </span>
              </>
            )}
            {git.bus_factor != null && (
              <>
                <span className="text-[var(--color-text-tertiary)]">Bus factor</span>
                <span className={`tabular-nums ${git.bus_factor <= 1 ? "text-red-400" : "text-[var(--color-text-secondary)]"}`}>
                  {git.bus_factor}
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Commit categories sparkline */}
      {git.commit_categories && Object.keys(git.commit_categories).length > 0 && (
        <div>
          <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
            Commit Types
          </p>
          <CommitCategorySparkline categories={git.commit_categories} />
        </div>
      )}

      {/* Top authors with bars */}
      {git.top_authors && git.top_authors.length > 0 && (
        <div>
          <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
            Authors
          </p>
          <div className="space-y-2">
            {git.top_authors.slice(0, 4).map((author) => (
              <div key={author.email} className="space-y-0.5">
                <div className="flex items-center gap-2 text-xs">
                  <AuthorAvatar name={author.name} />
                  <span className="flex-1 truncate text-[var(--color-text-secondary)]">
                    {author.name}
                  </span>
                  <span className="text-[var(--color-text-tertiary)] tabular-nums shrink-0">
                    {Math.round(author.pct * 100)}%
                  </span>
                </div>
                <div className="h-1 w-full rounded-full bg-[var(--color-bg-elevated)] ml-7">
                  <div
                    className="h-1 rounded-full transition-all"
                    style={{
                      width: `${Math.min(100, author.pct * 100)}%`,
                      backgroundColor: AUTHOR_BAR_COLORS[authorColorIndex(author.name)],
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Co-change partners */}
      {git.co_change_partners && git.co_change_partners.length > 0 && (
        <div>
          <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
            Co-change Partners
          </p>
          <CoChangeList partners={git.co_change_partners} />
        </div>
      )}

      {/* Recent commits */}
      {commits.length > 0 && (
        <div>
          <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
            Recent Commits
          </p>
          <ul className="space-y-2.5">
            {commits.slice(0, 6).map((commit) => (
              <li key={commit.sha} className="flex gap-2 min-w-0">
                <div className="flex flex-col items-center shrink-0">
                  <GitCommit className="h-3.5 w-3.5 text-[var(--color-text-tertiary)] mt-0.5" />
                  <div className="w-px flex-1 bg-[var(--color-border-default)] mt-1" />
                </div>
                <div className="min-w-0 pb-2">
                  <p className="text-xs text-[var(--color-text-secondary)] leading-snug line-clamp-3 break-words" title={commit.message}>
                    {commit.message}
                  </p>
                  <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                    <span className="font-mono text-[10px] text-[var(--color-accent-primary)]">
                      {commit.sha.slice(0, 7)}
                    </span>
                    <span className="text-[10px] text-[var(--color-text-tertiary)] truncate max-w-[120px]" title={commit.author}>
                      {commit.author}
                    </span>
                    <span className="text-[10px] text-[var(--color-text-tertiary)]">
                      {formatRelativeTime(commit.date)}
                    </span>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {commits.length === 0 && (!git.top_authors || git.top_authors.length === 0) && (
        <p className="text-xs text-[var(--color-text-tertiary)]">No commit history available.</p>
      )}
    </div>
  );
}
