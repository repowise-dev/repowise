import type * as React from "react";
import { Bug } from "lucide-react";
import { AskAboutThis } from "../chat/ask-about-this";
import { AgentBadge, NewContributorBadge, isNewContributor } from "./agent-badge";
import { PriorityBadge } from "./priority-badge";
import { RiskDriverBreakdown, describeDriver } from "./risk-driver-breakdown";
import { CommitFilesTable } from "./commit-files-table";
import { FindingsByFile } from "./findings-by-file";
import { betterSentence, commitDelta, readCommitHealth, worseSentence } from "./commit-health-copy";
import { HealthDeltaLede, type HealthDeltaLedeCopy } from "./health-delta-lede";
import type { HealthDeltaLabels } from "./health-delta-mark";
import { OverviewSection } from "../overview/section";
import { formatDateTime } from "../lib/format";
import type { CommitDetail } from "@repowise-dev/types/git";

/** Above this many files, the list starts collapsed. */
const LONG_FILE_LIST = 10;

export interface CommitDetailCardProps {
  commit: CommitDetail;
  /** Rank of this change's fix density against recent commits, when a live
   *  scorer supplied one. Drives only the reconciliation sentence. */
  fixPercentile?: number | null | undefined;
  /** The primary action beside the verdict, typically the agent prompt. */
  action?: React.ReactNode;
  /** Words for the health mark beside the lede figure. */
  healthLabels?: Partial<HealthDeltaLabels> | undefined;
  /** The lede's words; the host's translations, English by default. */
  ledeCopy?: Partial<HealthDeltaLedeCopy> | undefined;
  /** Where to fix a file's findings (its health view). Without it the
   *  "Got worse" groups carry no link. */
  fileHref?: ((path: string) => string) | undefined;
  /** The file link's word; English by default. */
  openFileLabel?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  className?: string;
}

/**
 * Drill-down for one commit, in the order a reader acts on it: what it did to
 * code health (the verdict, then "Got better", then "Got worse" with each
 * finding's file and line), then the diff-shape review priority as context.
 */
export function CommitDetailCard({
  commit,
  fixPercentile,
  action,
  healthLabels,
  ledeCopy,
  fileHref,
  openFileLabel = "Open file",
  LinkComponent,
  className,
}: CommitDetailCardProps) {
  const A = LinkComponent ?? "a";
  const c = commit;
  const files = c.files ?? [];
  const health = c.health ?? null;
  const delta = commitDelta(c.health_delta, health);
  const read = readCommitHealth(c.health_delta, health);
  const counted = read.kind !== null && !read.stale;
  const improvedFiles = (c.health_delta?.files ?? []).filter((f) => (f.change ?? 0) > 0);
  const reconciled = reconcile(c, fixPercentile ?? null);

  return (
    <div className={className}>
      <CommitHeader commit={c} />

      <div className="mt-7">
        <HealthDeltaLede
          delta={delta}
          copy={{ label: "What this commit did to code health", ...ledeCopy }}
          markLabels={healthLabels}
          action={action}
        />
      </div>

      {counted && delta && read.better > 0 && (
        <OverviewSection
          className="mt-7"
          title="Got better"
          description={betterSentence(delta)}
        >
          {improvedFiles.length > 0 && (
            <ul className="space-y-1 font-mono text-[11px] text-[var(--color-text-secondary)]">
              {improvedFiles.map((f) => (
                <li key={f.path} className="[overflow-wrap:anywhere]">
                  {f.path}{" "}
                  <span className="tabular-nums text-[var(--color-success)]">
                    {f.before?.toFixed(1)} to {f.after?.toFixed(1)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </OverviewSection>
      )}

      {counted && health && health.findings.length > 0 && (
        <OverviewSection className="mt-7" title="Got worse" description={worseSentence(health)}>
          <FindingsByFile
            findings={health.findings}
            fileAction={(g) =>
              fileHref ? (
                <A
                  href={fileHref(g.path)}
                  className="text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:underline focus-visible:text-[var(--color-accent-primary)]"
                >
                  {openFileLabel}
                </A>
              ) : null
            }
          />
        </OverviewSection>
      )}

      <OverviewSection
        className="mt-7"
        title="Risk context"
        description={riskSentence(c)}
        hint={`${Math.round(c.risk_percentile)}th percentile`}
        collapsible
        // Open only when health has nothing to say: then it is the only signal.
        defaultOpen={read.kind === null}
      >
        <div className="space-y-3 text-xs text-[var(--color-text-secondary)]">
          <p className="flex flex-wrap items-center gap-2">
            <span className="tabular-nums">
              Review priority: {Math.round(c.risk_percentile)}th percentile in this repo
            </span>
            <PriorityBadge priority={c.review_priority} />
          </p>
          {reconciled && <p>{reconciled}</p>}
          {/* The drivers explain a diff-size statistic that ranks 0.99 against
              lines added, so they are transparency about the model rather
              than the finding a reviewer opened this for. */}
          <details className="group">
            <summary className="cursor-pointer text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
              How the diff-shape rank was computed
            </summary>
            <p className="mt-2 max-w-[62ch] text-[var(--color-text-tertiary)]">
              The measurements behind the rank, and the signed points each moved it
              by against the model&apos;s baseline commit. File, directory and
              subsystem counts are left out: they enter the score, but their fitted
              signs are collinearity with diff size rather than a finding.
            </p>
            <RiskDriverBreakdown className="mt-3" drivers={c.drivers} />
          </details>
        </div>
      </OverviewSection>

      {files.length > 0 && (
        <OverviewSection
          className="mt-7"
          title="Where this change lands"
          description="The files this commit touched, and how much bug-fix history each one carries. Unlike the percentile above, this does not grow with the size of the diff."
          hint={`${files.length} file${files.length === 1 ? "" : "s"}`}
          collapsible
          // A long file list is the thing that buries everything below it.
          defaultOpen={files.length <= LONG_FILE_LIST}
        >
          <CommitFilesTable files={files} />
        </OverviewSection>
      )}
    </div>
  );
}

function CommitHeader({ commit: c }: { commit: CommitDetail }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <span className="font-mono text-xs text-[var(--color-text-secondary)]">{c.short_sha}</span>
        {c.is_fix && (
          <span className="inline-flex items-center gap-1 text-xs text-[var(--color-error)]">
            <Bug className="h-3 w-3" />
            fix
          </span>
        )}
        {c.agent_name && (
          <AgentBadge agentName={c.agent_name} tier={c.agent_autonomy_tier} confidence={c.agent_confidence} />
        )}
        {!c.agent_name && isNewContributor(c.author_commit_count) && (
          <NewContributorBadge commitCount={c.author_commit_count as number} />
        )}
        <AskAboutThis
          context={{ kind: "commit", label: c.short_sha, target: c.sha, targetKind: "commit" }}
          question={`Summarize the intent and impact of commit ${c.short_sha}, and say which files deserve the closest review.`}
          label={`Ask about commit ${c.short_sha}`}
          className="h-6 w-6"
        />
      </div>
      {/* The subject wraps: the one thing here a reader must read in full. */}
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
