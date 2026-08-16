import { GitBranch } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { CommitCategorySparkline } from "../git/commit-category-sparkline";
import { AgentTierBar } from "../git/agent-tier-bar";
import { OwnershipDonut } from "../git/ownership-donut";
import { ChangeHistoryCard } from "../git/change-history-card";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { summarizeFixHistory } from "../lib/fix-history";
import { formatAgeDays, formatNumber, formatRelativeTime, truncatePath } from "../lib/format";
import type { FileDetailGit } from "@repowise-dev/types/files";
import { FileSection, Fig } from "./file-section";
import { FileMark } from "./file-marks";

/** Matches `FixHistoryBadge`'s default tooltip — same claim, same wording. */
const FIX_HISTORY_TITLE =
  "Bug-fix commits that touched this file in the trailing defect window.";

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
        titleAs="h2"
        icon={<GitBranch className="h-8 w-8" />}
        title="No git history for this file"
        description="Commits, authors and co-change partners land the first time the repository's history is indexed."
      />
    );
  }

  const hasCategories = Object.values(git.commit_categories ?? {}).some((v) => v > 0);
  const agentPct =
    git.agent.agent_authored_pct != null ? Math.round(git.agent.agent_authored_pct * 100) : null;
  // Same helper the wiki sidebar uses, so one file's history reads identically
  // on both surfaces. Null means nothing counted: render nothing.
  const summary = summarizeFixHistory(git.prior_defect_count, git.last_fix_at, git.bug_magnet);
  // A file whose git indexing never completed lands with zeroes across the
  // board, and `formatAgeDays(0)` reads "< 1 day". Anchor the age to having
  // at least one commit so an unindexed file is not called brand new.
  const commits = git.commit_count_total ?? 0;
  const age =
    commits > 0 && Number.isFinite(git.age_days) ? formatAgeDays(git.age_days as number) : null;

  const stats: RibbonStat[] = [
    {
      label: "Commits",
      value: formatNumber(commits),
      ...(age ? { sub: `over ${age}` } : {}),
    },
    { label: "Last 90 days", value: formatNumber(git.commit_count_90d) },
    { label: "Contributors", value: formatNumber(git.contributor_count) },
    { label: "Bus factor", value: formatNumber(git.bus_factor) },
  ];
  if (git.churn_percentile != null) {
    stats.push({ label: "Churn", value: `${Math.round(git.churn_percentile)}th pct` });
  }

  return (
    <div>
      <FileSection
        first
        title="Change rhythm"
        description={
          <>
            Mined from the full git history Repowise indexed. Churn is a percentile against every
            other file in the repository, and bus factor is how many people would have to leave
            before nobody knew this file.
          </>
        }
      >
        {/* `FixHistoryBadge` renders the same summary as a bordered, tinted
            `Badge`. Beside a `FileMark` — which is what the header row above
            uses for exactly this job — that is two vocabularies for "one thing
            worth knowing about this file", and the bordered one is the pill
            rule 9 kills. `summarizeFixHistory` is the shared copy rule, so
            reading it directly keeps the wording identical to the badge's
            everywhere the badge still ships. */}
        {(summary || git.is_stable) && (
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            {git.is_stable && (
              <FileMark title="No recent commits — this file has settled.">Stable</FileMark>
            )}
            {summary && (
              <FileMark title={FIX_HISTORY_TITLE}>
                {summary.magnet ? `Bug magnet · ${summary.label}` : summary.label}
              </FileMark>
            )}
          </div>
        )}

        <StatRibbon stats={stats} />

        {/* Self-contained: returns null when there is nothing to say, so an
            entropy-less, never-renamed file loses no space to it. No border
            box around it — the section's own rhythm is the grouping. */}
        <ChangeHistoryCard
          changeEntropyPct={git.change_entropy_pct ?? null}
          priorDefectCount={git.prior_defect_count ?? null}
          lastFixAt={git.last_fix_at ?? null}
          originalPath={git.original_path ?? null}
          commitCountCapped={git.commit_count_capped ?? false}
        />

        {hasCategories && <CommitCategorySparkline categories={git.commit_categories} />}
      </FileSection>

      {git.significant_commits.length > 0 && (
        <FileSection
          title="Significant commits"
          description="The commits that moved this file the most, largest first."
        >
          <ul className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
            {git.significant_commits.slice(0, 8).map((c) => (
              <li key={c.sha}>
                <a
                  href={`${linkPrefix}/commits?commit=${c.sha}`}
                  className="-mx-2 flex items-baseline gap-3 rounded px-2 py-2.5 transition-colors hover:bg-[var(--color-bg-elevated)]"
                >
                  <span className="shrink-0 font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
                    {c.sha.slice(0, 8)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm text-[var(--color-text-primary)]">
                    {c.message}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-tertiary)]">
                    {c.date ? formatRelativeTime(c.date) : ""}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </FileSection>
      )}

      <FileSection
        title="Who writes it"
        description={
          <>
            Commit share by author across the indexed history
            {git.agent.agent_commit_count > 0 && (
              <>
                , and how much of it came from a coding agent —{" "}
                <Fig>{formatNumber(git.agent.agent_commit_count)}</Fig>{" "}
                {git.agent.agent_commit_count === 1 ? "commit" : "commits"}
                {agentPct != null && <> ({agentPct}%)</>}
              </>
            )}
            .
          </>
        }
      >
        <div className="grid grid-cols-1 gap-x-10 gap-y-8 lg:grid-cols-2">
          <div className="min-w-0">
            {git.top_authors.length === 0 ? (
              <p className="text-sm text-[var(--color-text-tertiary)]">
                Author attribution lands with the next index.
              </p>
            ) : (
              <OwnershipDonut
                slices={git.top_authors.map((a) => ({ name: a.name, value: a.commit_count }))}
              />
            )}
          </div>
          <div className="min-w-0">
            <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Agent share
            </p>
            {git.agent.agent_commit_count === 0 ? (
              <p className="mt-2 text-sm text-[var(--color-text-tertiary)]">
                No agent-attributed commits on this file.
              </p>
            ) : (
              Object.keys(git.agent.tier_counts).length > 0 && (
                <div className="mt-3">
                  <AgentTierBar tierCounts={git.agent.tier_counts} />
                </div>
              )
            )}
          </div>
        </div>
      </FileSection>

      <FileSection
        title="Changes together with"
        description={
          git.co_change_partners.length > 0 ? (
            <>
              Files that land in the same commit as this one. The count is how many commits touched
              both — a partner you did not expect is usually a coupling nobody wrote down.
            </>
          ) : (
            "Files that repeatedly land in the same commit as this one appear here once the history has enough commits to be sure."
          )
        }
      >
        {git.co_change_partners.length > 0 && (
          <ul className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
            {git.co_change_partners.slice(0, 8).map((p) => (
              <li key={p.file_path}>
                <a
                  href={partnerHref(p.file_path)}
                  className="-mx-2 flex items-baseline justify-between gap-3 rounded px-2 py-2.5 transition-colors hover:bg-[var(--color-bg-elevated)]"
                >
                  <span
                    className="min-w-0 truncate font-mono text-xs text-[var(--color-text-primary)]"
                    title={p.file_path}
                  >
                    {truncatePath(p.file_path, 44)}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
                    {p.co_change_count}&times;
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </FileSection>
    </div>
  );
}
