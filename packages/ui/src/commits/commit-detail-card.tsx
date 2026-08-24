import { Bug } from "lucide-react";
import { AgentBadge, NewContributorBadge, isNewContributor } from "./agent-badge";
import { PriorityBadge } from "./priority-badge";
import { RiskDriverBreakdown, describeDriver } from "./risk-driver-breakdown";
import { PageLede } from "../shared/page-lede";
import { OverviewSection } from "../overview/section";
import { formatDateTime } from "../lib/format";
import type { CommitDetail } from "@repowise-dev/types/git";

export interface CommitDetailCardProps {
  commit: CommitDetail;
  /**
   * Raw score at this repo's moderate/high boundary — `CommitStats.high_cut`.
   * Optional: without it the card states the score on its own rather than
   * inventing a comparison.
   */
  reviewCut?: number | null | undefined;
  className?: string;
}

/**
 * Drill-down for one commit.
 *
 * The benchmarked repo-relative percentile and its server-owned priority lead.
 * The supporting raw score remains visible with its per-commit unit and
 * diff-shape interpretation, never as a probability.
 */
export function CommitDetailCard({ commit, reviewCut, className }: CommitDetailCardProps) {
  const c = commit;

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
          <p>{riskSentence(c, reviewCut)}</p>
          {c.change_risk_score != null && (
            <p>
              Supporting diff-size score: {c.change_risk_score.toFixed(1)} out of 10,
              calibrated per commit and not a probability.
            </p>
          )}
        </PageLede>
      </div>

      {/* No stat ribbon above this, deliberately. The model's feature set is
          lines added, lines deleted, files, directories, subsystems, scatter
          and author experience — which is every figure a ribbon here could
          carry. A row of them above this table restates the table, and does it
          without the one thing the table adds: what each measurement did to
          the score. `CommitsLede` skipped its ribbon for the same reason. */}
      <OverviewSection
        className="mt-7"
        title="What changed, and what it cost"
        description="The measurements that explain the score, and the exact signed points each one moved it by. Red raised it, green lowered it, both against the model's baseline commit. File, directory and subsystem counts are left out: they enter the score, but their fitted signs are collinearity with diff size rather than a finding."
      >
        <RiskDriverBreakdown drivers={c.drivers} />
      </OverviewSection>
    </div>
  );
}

/**
 * The sentence that makes the score mean something.
 *
 * It explains where the commit sits in this repo. The headline already carries
 * the exact percentile; this sentence explains its tercile without inventing a
 * defect probability from the supporting model score.
 */
function riskSentence(c: CommitDetail, reviewCut: number | null | undefined): string {
  const tercile: Record<string, string> = {
    high: "sits in the top third of this repo's own diff-shape distribution, the review-priority band worth reviewing",
    moderate:
      "sits in the middle third of this repo's own diff-shape distribution, so its review priority is typical here",
    low: "sits in the bottom third of this repo's own diff-shape distribution",
  };
  let out = `This commit ${tercile[c.review_priority] ?? tercile.moderate}`;

  // `high_cut` is the moderate/high boundary and nothing else, so it only
  // describes where *this* commit's band begins when the commit is in the top
  // one. Appending it to a moderate commit would name the middle third's floor
  // as a number that is actually its ceiling.
  if (reviewCut != null && c.review_priority === "high") {
    out += `, which here starts at ${reviewCut.toFixed(1)} out of 10`;
  }
  out += ".";

  if (c.change_risk_score != null) {
    // `drivers` arrive strongest-first, and only score-raising ones explain
    // why the score landed where it did.
    const raising = c.drivers.filter((d) => d.value !== null && d.contribution > 0);
    if (raising.length === 0) {
      out += " The raw score stays low across every driver.";
    } else {
      // The same wording as the table below, not the server's baseline-relative
      // labels. Two vocabularies for one set of drivers, a paragraph apart,
      // reads as two different lists.
      const reasons = raising.slice(0, 2).map(describeDriver).join(" and ");
      out += ` What pushed the raw score up was mainly ${reasons}. That score is measured against the model's baseline commit rather than against this repo, so read it as the shape of the change rather than a verdict on it.`;
    }
  }

  if (!c.agent_name && isNewContributor(c.author_commit_count)) {
    out += " The author is new to this code.";
  }
  return out;
}
