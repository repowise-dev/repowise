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
 * Rebuilt on the section style, and reorganised around one rule: say each fact
 * once. The card used to state the percentile four times over — a 24px
 * `100%ile`, a priority pill, "Riskier than 100% of this repo's commits", and
 * then "Riskier than most commits in this repo (100th percentile)" — with the
 * summary paragraph restating the box immediately above it.
 *
 * What leads is now the raw score against this repo's own review line, for two
 * reasons. The queue this sheet opens from already carries a percentile column
 * per row, so repeating it here spends the largest figure on the page on
 * something the reader just clicked past. And the percentile cannot honestly be
 * turned into a rank: the raw score is rounded to one decimal before ranking,
 * so 886 commits share 86 distinct percentiles, and "the 1st riskiest of 886"
 * would be a computed guess printed as a measurement.
 *
 * The tercile still appears, once, as the badge beside the figure.
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
        <PageLede
          label="Change-risk score"
          value={c.change_risk_score != null ? c.change_risk_score.toFixed(1) : "—"}
          unit="out of 10"
          badge={<PriorityBadge priority={c.review_priority} />}
        >
          <p>{riskSentence(c, reviewCut)}</p>
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
        description="Every measurement the model reads, and the exact signed points each one moved the raw score by. Red raised it, green lowered it, both against the model's baseline commit."
      >
        <RiskDriverBreakdown drivers={c.drivers} />
      </OverviewSection>
    </div>
  );
}

/**
 * The sentence that makes the score mean something.
 *
 * It leads with where the commit sits in this repo rather than with the raw
 * number, because the raw number is anchored to a calibration corpus and skews
 * high on any repo whose typical commit is large — this index has 296 of 884
 * commits in the top tercile and a score distribution piled against the 10
 * ceiling. The percentile is deliberately absent: the queue row the reader
 * arrived from already carried it.
 */
function riskSentence(c: CommitDetail, reviewCut: number | null | undefined): string {
  const tercile: Record<string, string> = {
    high: "sits in the top third of this repo's own risk distribution, the band worth reviewing",
    moderate:
      "sits in the middle third of this repo's own risk distribution, so it is about as risky as the work around it",
    low: "sits in the bottom third of this repo's own risk distribution",
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
    // `drivers` arrive strongest-first, and only the risk-raising ones explain
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
