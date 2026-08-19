import * as React from "react";
import type { CommitStats } from "@repowise-dev/types/git";
import { PageLede } from "../shared/page-lede";
import { ReadsColumn, type ReadItem } from "../overview/reads-column";

export interface CommitsLedeProps {
  stats: CommitStats;
  /** `/repos/{id}`, for the read-column drill-ins. */
  base: string;
  LinkComponent?: React.ElementType | undefined;
}

function pct(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}

/**
 * What the commit history looks like, as one figure and a sentence.
 *
 * Replaces four `MetricCard`s of equal weight, which gave the page nothing to
 * lead with: "Commits 4,912 / High priority 1,637 / Fix commits 1,204 / Avg
 * entropy 0.42" is four numbers at 24px, one of them a bare 0.42 whose
 * two-word caption ("change diffusion") did not say what it measured or which
 * direction was bad.
 *
 * The headline is the review backlog rather than the commit count, because
 * that is the thing this page exists to work through. Every figure here is
 * server-computed over the whole history: reducing the loaded page instead
 * would under-count fixes and, since the feed defaults to risk-sorted and is
 * therefore entirely top-tercile, would report the high-priority share as
 * roughly 100%.
 *
 * No median score: `CommitStats` does not carry one, and deriving it from the
 * histogram bins would be an estimate printed as a measurement.
 */
export function CommitsLede({ stats, base, LinkComponent }: CommitsLedeProps) {
  const total = stats.total_commits;
  const highPct = pct(stats.high_priority_count, total);
  const fixPct = pct(stats.fix_commit_count, total);
  const agentPct = pct(stats.agent_commit_count, total);

  const reads: ReadItem[] = [
    {
      key: "fixes",
      label: "Fix commits",
      value: stats.fix_commit_count.toLocaleString(),
      unit: `${fixPct}%`,
      why: "Commits whose subject reads as a bug fix rather than new work.",
      href: `${base}/code-health?tab=triage`,
    },
  ];
  if (stats.agent_commit_count > 0) {
    reads.push({
      key: "agent",
      label: "Written by an agent",
      value: stats.agent_commit_count.toLocaleString(),
      unit: `${agentPct}%`,
      why: "Read from commit trailers, so it counts what was declared.",
      href: `${base}/commits`,
    });
  }
  reads.push({
    key: "entropy",
    label: "Change diffusion",
    value: stats.avg_entropy.toFixed(2),
    unit: "bits",
    // Shannon entropy of the per-file churn distribution, in bits, and
    // deliberately not normalised (see _entropy in change_risk/features.py).
    // It has no 1.0 ceiling: k equally-changed files score log2(k), so this
    // repo's 2.08 is roughly four files' worth of spread.
    why: "Shannon entropy of a commit's churn across its files. Zero is a single file, and every extra bit is a doubling of how widely the change spread.",
    href: `${base}/architecture?view=coupling`,
  });
  if (stats.high_cut != null) {
    reads.push({
      key: "cut",
      label: "Review threshold",
      value: stats.high_cut.toFixed(1),
      unit: "out of 10",
      why: "Score a commit has to clear to land in this repo's top tercile.",
      href: `${base}/code-health?tab=triage`,
    });
  }

  // No StatRibbon here, deliberately. `CommitStats` carries six measured
  // figures and the lede plus this column already spend all six, so a ribbon
  // under them could only restate what is directly above it — which is the
  // box soup this redesign exists to remove, wearing hairlines instead of
  // borders. The contributor page keeps its ribbon because that payload has
  // figures left over.
  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <section className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-10">
        <PageLede
          label="Needs review"
          value={stats.high_priority_count.toLocaleString()}
          unit={`of ${total.toLocaleString()} scored`}
        >
          <p>
            <strong className="font-semibold text-[var(--color-text-primary)]">
              {stats.high_priority_count.toLocaleString()} commits
            </strong>{" "}
            sit in this repo&apos;s top risk tercile, which is {highPct}% of the{" "}
            {total.toLocaleString()} scored. The cut is drawn against this
            codebase&apos;s own history rather than a global curve, so a quiet repo
            still fills its top band
            {stats.high_cut != null
              ? `, and here it starts at ${stats.high_cut.toFixed(1)} out of 10`
              : ""}
            . What pushes a commit up is size and spread together: a large change
            confined to one area scores below a smaller one scattered across a
            dozen files.
          </p>
        </PageLede>
        <ReadsColumn items={reads} LinkComponent={LinkComponent} />
      </section>
    </div>
  );
}
