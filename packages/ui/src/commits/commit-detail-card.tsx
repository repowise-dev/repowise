import { Bug, GitCommitHorizontal, User } from "lucide-react";
import { AgentBadge, NewContributorBadge } from "./agent-badge";
import { PriorityBadge } from "./priority-badge";
import { RiskDriverBreakdown } from "./risk-driver-breakdown";
import { formatDateTime, formatLOC } from "../lib/format";
import type { CommitDetail } from "@repowise-dev/types/git";

export interface CommitDetailCardProps {
  commit: CommitDetail;
  className?: string;
}

/** 1 -> "1st", 2 -> "2nd", 93 -> "93rd", 11 -> "11th". */
function ordinal(n: number): string {
  const suffix =
    n % 100 >= 10 && n % 100 <= 20
      ? "th"
      : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] ?? "th");
  return `${n}${suffix}`;
}

/** Lead clause: where this commit sits in its own repo's risk distribution. */
const PRIORITY_LEAD: Record<string, string> = {
  low: "Lower risk than a typical commit in this repo",
  moderate: "About as risky as a typical commit in this repo",
  high: "Riskier than most commits in this repo",
};

/**
 * One-line, plain-English read of the score: the repo-relative standing first
 * (the signal users should act on), then a short, honest note on why the raw
 * model score landed where it did. Built entirely from fields already on the
 * commit — no extra fetch.
 */
function riskSummary(c: CommitDetail): string {
  const lead = PRIORITY_LEAD[c.review_priority] ?? PRIORITY_LEAD.moderate;
  let out = `${lead} (${ordinal(Math.round(c.risk_percentile))} percentile).`;

  if (c.change_risk_score != null) {
    // `drivers` arrive strongest-first; the risk-raising ones explain the score.
    const raising = c.drivers.filter(
      (d) => d.value !== null && d.contribution > 0,
    );
    const raw = c.change_risk_score.toFixed(1);
    if (raising.length === 0) {
      out += ` Its raw model score (${raw}/10) stays low across every driver.`;
    } else {
      const reasons = raising
        .slice(0, 2)
        .map((d) => d.label)
        .join(" and ");
      out += ` Its raw model score (${raw}/10) is driven mainly by ${reasons}`;
      out +=
        ", measured against the model's baseline commit rather than this repo.";
    }
  }

  if (c.author_experience != null && c.author_experience < 3) {
    out += " The author is new to this code.";
  }
  return out;
}

function Stat({
  label,
  value,
  title,
}: {
  label: string;
  value: string;
  title?: string;
}) {
  return (
    <div title={title}>
      <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {label}
      </div>
      <div className="text-sm tabular-nums text-[var(--color-text-primary)]">
        {value}
      </div>
    </div>
  );
}

/**
 * Drill-down for one commit: header, repo-relative risk summary, the Kamei
 * change features, and the exact per-feature risk-driver breakdown.
 */
export function CommitDetailCard({ commit, className }: CommitDetailCardProps) {
  const c = commit;
  return (
    <div className={className}>
      {/* Header */}
      <div className="mb-4 space-y-1.5">
        <div className="flex items-center gap-2">
          <GitCommitHorizontal className="h-4 w-4 text-[var(--color-text-tertiary)]" />
          <span className="font-mono text-xs text-[var(--color-text-secondary)]">
            {c.short_sha}
          </span>
          {c.is_fix && (
            <span className="inline-flex items-center gap-1 text-xs text-red-400">
              <Bug className="h-3 w-3" />
              fix
            </span>
          )}
        </div>
        <p className="text-sm font-medium text-[var(--color-text-primary)] break-words">
          {c.subject || "(no subject)"}
        </p>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
          <span className="inline-flex items-center gap-1">
            <User className="h-3 w-3" />
            {c.author_name || "unknown"}
          </span>
          {c.agent_name && (
            <AgentBadge
              agentName={c.agent_name}
              tier={c.agent_autonomy_tier}
              confidence={c.agent_confidence}
            />
          )}
          {!c.agent_name &&
            c.author_experience != null &&
            c.author_experience < 3 && (
              <NewContributorBadge experience={c.author_experience} />
            )}
          {c.committed_at && <span>{formatDateTime(c.committed_at)}</span>}
        </div>
        {c.agent_name && c.agent_channel && (
          <p className="text-[10px] text-[var(--color-text-tertiary)]">
            Attribution channel: {c.agent_channel}
            {c.agent_confidence ? ` · ${c.agent_confidence} confidence` : ""}
          </p>
        )}
      </div>

      {/* Risk summary */}
      <div className="mb-4 flex items-center gap-4 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3">
        <div className="text-center">
          <div className="text-2xl font-semibold tabular-nums text-[var(--color-text-primary)]">
            {c.risk_percentile.toFixed(0)}
            <span className="text-sm text-[var(--color-text-tertiary)]">
              %ile
            </span>
          </div>
          <div className="mt-0.5">
            <PriorityBadge priority={c.review_priority} />
          </div>
        </div>
        <div className="flex-1 text-xs text-[var(--color-text-secondary)]">
          Riskier than{" "}
          <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
            {c.risk_percentile.toFixed(0)}%
          </span>{" "}
          of this repo&apos;s commits.
          {c.change_risk_score != null && (
            <span
              className="ml-1 text-[var(--color-text-tertiary)]"
              title="Raw calibrated score (0–10). Anchored to the calibration corpus, so it skews high on repos with large typical commits — use the repo-relative percentile above for review order."
            >
              (raw {c.change_risk_score.toFixed(1)}/10)
            </span>
          )}
        </div>
      </div>

      {/* Plain-English read — repo-relative standing first, then the why. */}
      <p className="mb-4 text-xs leading-relaxed text-[var(--color-text-secondary)]">
        {riskSummary(c)}
      </p>

      {/* Kamei change features */}
      <div className="mb-4 grid grid-cols-3 gap-3 sm:grid-cols-4">
        <Stat
          label="Lines"
          value={`+${formatLOC(c.lines_added)} -${formatLOC(c.lines_deleted)}`}
        />
        <Stat label="Files" value={String(c.files_changed)} />
        <Stat
          label="Dirs"
          value={String(c.dirs_changed)}
          title="Distinct directories touched"
        />
        <Stat
          label="Subsystems"
          value={String(c.subsystems_changed)}
          title="Distinct top-level subsystems touched"
        />
        <Stat
          label="Entropy"
          value={c.entropy.toFixed(2)}
          title="Shannon entropy of the per-file churn distribution — how scattered the change is"
        />
        {c.author_experience != null && (
          <Stat
            label="Author exp"
            value={String(c.author_experience)}
            title="The author's cumulative prior-commit count at the time of this commit"
          />
        )}
      </div>

      {/* Risk drivers */}
      <div>
        <p className="mb-1 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Why this score
        </p>
        <p className="mb-2 text-[10px] text-[var(--color-text-tertiary)]">
          Each driver compares this change to the model&apos;s baseline commit.
          Red raised the raw score; green lowered it.
        </p>
        <RiskDriverBreakdown drivers={c.drivers} />
      </div>
    </div>
  );
}
