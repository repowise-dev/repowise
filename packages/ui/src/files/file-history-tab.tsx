import { GitBranch, Bot } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { CommitCategorySparkline } from "../git/commit-category-sparkline";
import { AgentTierBar } from "../git/agent-tier-bar";
import { OwnershipDonut } from "../git/ownership-donut";
import { ChangeHistoryCard } from "../git/change-history-card";
import { FixHistoryBadge } from "../git/fix-history-badge";
import { StatGrid, StatTile } from "../shared/stat-grid";
import { Badge } from "../ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { summarizeFixHistory } from "../lib/fix-history";
import { formatAgeDays, formatRelativeTime, truncatePath } from "../lib/format";
import type { FileDetailGit } from "@repowise-dev/types/files";

interface FileHistoryTabProps {
  git: FileDetailGit | null;
  linkPrefix: string;
  /** Build a file-page href for a co-change partner. */
  partnerHref: (path: string) => string;
}

export function FileHistoryTab({ git, linkPrefix, partnerHref }: FileHistoryTabProps) {
  if (!git) {
    return (
      <EmptyState
        icon={<GitBranch className="h-8 w-8" />}
        title="No git history"
        description="Git metadata appears once the repository's history is indexed."
      />
    );
  }

  const hasCategories = Object.values(git.commit_categories ?? {}).some((v) => v > 0);
  const agentPct =
    git.agent.agent_authored_pct != null ? Math.round(git.agent.agent_authored_pct * 100) : null;
  // Same helper the wiki sidebar uses, so one file's fix history reads
  // identically on both surfaces. Null means no counted fixes: render nothing.
  const fix = summarizeFixHistory(git.prior_defect_count, git.last_fix_at, git.bug_magnet);
  // A file whose git indexing never completed lands with zeroes across the
  // board, and `formatAgeDays(0)` reads "< 1 day". Anchor the age to having
  // at least one commit so an unindexed file is not called brand new.
  const commits = git.commit_count_total ?? 0;
  const age =
    commits > 0 && Number.isFinite(git.age_days) ? formatAgeDays(git.age_days as number) : null;

  return (
    <div className="space-y-4">
      {(fix || git.is_stable) && (
        <div className="flex flex-wrap items-center gap-2">
          {git.is_stable && <Badge variant="fresh">Stable</Badge>}
          <FixHistoryBadge
            count={git.prior_defect_count ?? null}
            lastFixAt={git.last_fix_at ?? null}
            bugMagnet={git.bug_magnet ?? false}
          />
        </div>
      )}

      <StatGrid columns={4}>
        <StatTile
          label="Commits (total)"
          value={commits}
          {...(age ? { hint: `over ${age}` } : {})}
        />
        <StatTile label="Commits (90d)" value={git.commit_count_90d} />
        <StatTile label="Contributors" value={git.contributor_count} />
        <StatTile label="Bus factor" value={git.bus_factor} />
      </StatGrid>

      {/* Self-contained: returns null when there is nothing to say, so an
          entropy-less, never-fixed, never-renamed file loses no space to it. */}
      <ChangeHistoryCard
        changeEntropyPct={git.change_entropy_pct ?? null}
        priorDefectCount={git.prior_defect_count ?? null}
        lastFixAt={git.last_fix_at ?? null}
        originalPath={git.original_path ?? null}
        commitCountCapped={git.commit_count_capped ?? false}
        className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3"
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Significant commits</CardTitle>
          </CardHeader>
          <CardContent>
            {git.significant_commits.length === 0 ? (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                No significant commits recorded.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {git.significant_commits.slice(0, 8).map((c) => (
                  <li key={c.sha}>
                    <a
                      href={`${linkPrefix}/commits?commit=${c.sha}`}
                      className="flex items-center gap-2 -mx-2 px-2 py-1 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
                    >
                      <span className="text-xs font-mono text-[var(--color-text-tertiary)] shrink-0">
                        {c.sha.slice(0, 8)}
                      </span>
                      <span className="text-xs text-[var(--color-text-primary)] truncate flex-1">
                        {c.message}
                      </span>
                      <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0">
                        {c.date ? formatRelativeTime(c.date) : ""}
                      </span>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4">
          {hasCategories && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Commit mix</CardTitle>
              </CardHeader>
              <CardContent>
                <CommitCategorySparkline categories={git.commit_categories} />
              </CardContent>
            </Card>
          )}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <Bot className="h-4 w-4 text-[var(--color-text-secondary)]" />
                Agent share
              </CardTitle>
            </CardHeader>
            <CardContent>
              {git.agent.agent_commit_count === 0 ? (
                <p className="text-xs text-[var(--color-text-tertiary)]">
                  No agent-attributed commits on this file.
                </p>
              ) : (
                <div className="space-y-2">
                  <p className="text-xs text-[var(--color-text-secondary)]">
                    <span className="text-base font-semibold tabular-nums text-[var(--color-text-primary)]">
                      {git.agent.agent_commit_count}
                    </span>{" "}
                    agent commit{git.agent.agent_commit_count === 1 ? "" : "s"}
                    {agentPct != null && ` · ${agentPct}% of indexed history`}
                  </p>
                  {Object.keys(git.agent.tier_counts).length > 0 && (
                    <AgentTierBar tierCounts={git.agent.tier_counts} />
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Top authors</CardTitle>
          </CardHeader>
          <CardContent>
            {git.top_authors.length === 0 ? (
              <p className="text-xs text-[var(--color-text-tertiary)]">No author data.</p>
            ) : (
              <OwnershipDonut
                slices={git.top_authors.map((a) => ({
                  name: a.name,
                  value: a.commit_count,
                }))}
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Changes together with</CardTitle>
          </CardHeader>
          <CardContent>
            {git.co_change_partners.length === 0 ? (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                No co-change partners detected.
              </p>
            ) : (
              <ul className="space-y-2">
                {git.co_change_partners.slice(0, 8).map((p) => (
                  <li key={p.file_path}>
                    <a
                      href={partnerHref(p.file_path)}
                      className="flex items-center justify-between gap-2 -mx-2 px-2 py-1 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
                    >
                      <span className="font-mono text-xs text-[var(--color-text-primary)] truncate" title={p.file_path}>
                        {truncatePath(p.file_path, 44)}
                      </span>
                      <span className="text-xs tabular-nums text-[var(--color-text-tertiary)] shrink-0">
                        ×{p.co_change_count}
                      </span>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
