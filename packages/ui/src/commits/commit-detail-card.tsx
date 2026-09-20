import { Bug } from "lucide-react";
import { AskAboutThis } from "../chat/ask-about-this";
import { AgentBadge, NewContributorBadge, isNewContributor } from "./agent-badge";
import { PriorityBadge } from "./priority-badge";
import { RiskDriverBreakdown, describeDriver } from "./risk-driver-breakdown";
import { CommitFilesTable } from "./commit-files-table";
import { PageLede } from "../shared/page-lede";
import { OverviewSection } from "../overview/section";
import { formatDateTime } from "../lib/format";
import type { CommitDetail } from "@repowise-dev/types/git";

export interface CommitDetailCardProps {
  commit: CommitDetail;
  /** Rank of this change's fix density against recent commits, when a live
   *  scorer supplied one. Drives only the reconciliation sentence. */
  fixPercentile?: number | null | undefined;
  className?: string;
}

/**
 * Drill-down for one commit.
 *
 * The benchmarked repo-relative percentile and its server-owned priority lead.
 * The supporting raw score remains visible with its per-commit unit and
 * diff-shape interpretation, never as a probability.
 */
export function CommitDetailCard({
  commit,
  fixPercentile,
  className,
}: CommitDetailCardProps) {
  const c = commit;
  const files = c.files ?? [];

  return (
    <div className={className}>
      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <span className="font-mono text-xs text-[var(--color-text-secondary)]">
            {c.short_sha}
          </span>
          {c.is_fix && (
            <span className="inline-flex items-center gap-1 text-xs text-[var(--color-error)]">
              <Bug className="h-3 w-3" />
              fix
            </span>
          )}
          {c.agent_name && (
            <AgentBadge
              agentName={c.agent_name}
              tier={c.agent_autonomy_tier}
              confidence={c.agent_confidence}
            />
          )}
          {!c.agent_name && isNewContributor(c.author_commit_count) && (
            <NewContributorBadge commitCount={c.author_commit_count as number} />
          )}
          <AskAboutThis
            context={{
              kind: "commit",
              label: c.short_sha,
              target: c.sha,
              targetKind: "commit",
            }}
            question={`Summarize the intent and impact of commit ${c.short_sha}, and say which files deserve the closest review.`}
            label={`Ask about commit ${c.short_sha}`}
            className="h-6 w-6"
          />
        </div>
        {/* The subject wraps. It is the one thing on this sheet a reader has to
            be able to read in full, so it never gets an ellipsis. */}
        <p className="text-base font-semibold leading-snug text-[var(--color-text-primary)] [overflow-wrap:anywhere] [text-wrap:pretty]">
          {c.subject || "(no subject)"}
        </p>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {c.author_name || "unknown"}
          {c.committed_at ? ` · ${formatDateTime(c.committed_at)}` : ""}
        </p>
        {c.agent_name && c.agent_channel && (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Attributed through {c.agent_channel}
            {c.agent_confidence ? `, ${c.agent_confidence} confidence` : ""}
          </p>
        )}
      </div>

      <div className="mt-7">
        {/* Named for what it measures: diff size, not danger. */}
        <PageLede
          label="Review priority"
          value={`${Math.round(c.risk_percentile)}th`}
          unit="percentile in this repo"
          badge={<PriorityBadge priority={c.review_priority} />}
        >
          <p>{riskSentence(c)}</p>
          {reconcile(c, fixPercentile ?? null) && (
            <p>{reconcile(c, fixPercentile ?? null)}</p>
          )}
        </PageLede>
      </div>

      {files.length > 0 && (
        <OverviewSection
          className="mt-7"
          title="Where this change lands"
          description="The files this commit touched, and how much bug-fix history each one carries. Unlike the percentile above, this does not grow with the size of the diff."
        >
          <CommitFilesTable files={files} />
        </OverviewSection>
      )}

      {/* Collapsed, deliberately. The drivers explain a diff-size statistic
          that ranks 0.99 against lines added, so they are transparency about
          the model rather than the finding a reviewer opened this for. */}
      <details className="group mt-7">
        <summary className="cursor-pointer text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
          How the diff-shape rank was computed
        </summary>
        <p className="mt-2 max-w-[62ch] text-xs text-[var(--color-text-tertiary)]">
          The measurements behind the rank, and the signed points each moved it
          by against the model&apos;s baseline commit. File, directory and
          subsystem counts are left out: they enter the score, but their fitted
          signs are collinearity with diff size rather than a finding.
        </p>
        <RiskDriverBreakdown className="mt-3" drivers={c.drivers} />
      </details>
    </div>
  );
}

/**
 * Says so when the two rankings disagree.
 *
 * Diff shape and fix history answer different questions and routinely part
 * company; the sheet used to show one of them and leave the reader to assume
 * it was the whole answer.
 */
function reconcile(c: CommitDetail, fixPercentile: number | null): string | null {
  if (fixPercentile == null) return null;
  const gap = fixPercentile - c.risk_percentile;
  if (Math.abs(gap) < 25) return null;
  return gap > 0
    ? `Ordinary in size, but it lands in files with a heavy fix record — ${Math.round(fixPercentile)}th percentile for prior fixes. That is the signal worth reading here.`
    : `Large for this repo, but it lands in files that have rarely broken — ${Math.round(fixPercentile)}th percentile for prior fixes.`;
}

/**
 * The sentence that makes the score mean something.
 *
 * It explains where the commit sits in this repo. The headline already carries
 * the exact percentile; this sentence explains its tercile without inventing a
 * defect probability from the supporting model score.
 */
function riskSentence(c: CommitDetail): string {
  const tercile: Record<string, string> = {
    high: "sits in the top third of this repo's own diff-shape distribution, the review-priority band worth reviewing",
    moderate:
      "sits in the middle third of this repo's own diff-shape distribution, so its review priority is typical here",
    low: "sits in the bottom third of this repo's own diff-shape distribution",
  };
  let out = `This commit ${tercile[c.review_priority] ?? tercile.moderate}`;

  out += ".";

  // `drivers` arrive strongest-first, and only rank-raising ones explain where
  // the change landed. The same wording as the collapsed table below: two
  // vocabularies for one set of drivers reads as two different lists.
  const raising = c.drivers.filter((d) => d.value !== null && d.contribution > 0);
  if (raising.length > 0) {
    out += ` Mostly ${raising.slice(0, 2).map(describeDriver).join(" and ")}.`;
  }

  if (!c.agent_name && isNewContributor(c.author_commit_count)) {
    out += " The author is new to this code.";
  }
  return out;
}
