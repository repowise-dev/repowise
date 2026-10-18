import { Bug } from "lucide-react";
import { AgentBadge, NewContributorBadge, isNewContributor } from "./agent-badge";
import { priorityLabel } from "./priority-badge";
import { HealthDeltaMark, type HealthDeltaLabels } from "./health-delta-mark";
import { healthDeltaKind } from "./health-delta";
import type { ResponsiveColumn } from "../shared/responsive-table";
import { formatLOC, formatRelativeTime } from "../lib/format";
import type { Commit, ReviewPriority } from "@repowise-dev/types/git";

/** The commit list's columns, health first. Split from the table so the list
 *  and its controls stay small. */

/** Marked only when it matters: the commit made health worse (or mixed) and
 *  touched a hotspot file. */
export function worseInHotspot(c: Commit): boolean {
  if ((c.touches_hotspot ?? 0) === 0 || !c.health_delta) return false;
  const kind = healthDeltaKind(c.health_delta);
  return kind === "worsened" || kind === "mixed";
}

export type CommitRow = Commit & { _idx: number };

function priorityText(c: Commit): string {
  return `${Math.round(c.risk_percentile)}th · ${priorityLabel(c.review_priority as ReviewPriority)}`;
}

export function commitColumns(
  healthLabels: Partial<HealthDeltaLabels> | undefined,
  hotspotLabel: string,
): ResponsiveColumn<CommitRow>[] {
  return [
    {
      // The lead: what the commit did to code health. Green only for a real
      // improvement, red only for a regression, a word beside every dot.
      key: "health",
      header: "Health",
      headerClassName: "w-32",
      render: (c) => <HealthDeltaMark delta={c.health_delta} labels={healthLabels ?? {}} />,
    },
    {
      key: "commit",
      header: "Commit",
      cellClassName: "min-w-[200px] max-w-[460px]",
      render: (c) => (
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-[var(--color-text-tertiary)] shrink-0">
            {c.short_sha}
          </span>
          {c.is_fix && <Bug className="h-3 w-3 shrink-0 text-[var(--color-error)]" />}
          {/* Wraps rather than truncates. This is the primary column, and an
              ellipsis here reports a layout decision to the reader as a shorter
              commit message: "release: v0.4.0, language-support upgrades across
              all Goo…" is the page hiding the part that says which. */}
          <span className="min-w-0 text-xs text-[var(--color-text-primary)] [text-wrap:pretty]">
            {c.subject || "(no subject)"}
          </span>
          {/* An exception, not the default: only where a commit made health
              worse in a hotspot. Plain text, so it never borrows the chip's
              colours; the "Touched hotspots" filter covers the rest. */}
          {worseInHotspot(c) && (
            <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-tertiary)]">
              {hotspotLabel}
            </span>
          )}
        </div>
      ),
    },
    {
      key: "author",
      header: "Author",
      priority: 2,
      cellClassName: "max-w-[220px]",
      render: (c) => (
        <div className="flex items-center gap-1.5 min-w-0 text-xs text-[var(--color-text-secondary)]">
          <span className="truncate">{c.author_name || "—"}</span>
          {c.agent_name && <AgentBadge agentName={c.agent_name} tier={c.agent_autonomy_tier} />}
          {!c.agent_name && isNewContributor(c.author_commit_count) && (
            <NewContributorBadge commitCount={c.author_commit_count as number} />
          )}
        </div>
      ),
      mobileRender: (c) => c.author_name || "—",
    },
    {
      key: "when",
      header: "When",
      headerClassName: "w-24",
      priority: 3,
      render: (c) => (
        <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {c.committed_at ? formatRelativeTime(c.committed_at) : "—"}
        </span>
      ),
    },
    {
      key: "lines",
      header: "Lines",
      headerClassName: "w-24",
      priority: 3,
      render: (c) => (
        <span className="text-xs tabular-nums">
          <span className="text-[var(--color-success)]">+{formatLOC(c.lines_added)}</span>{" "}
          <span className="text-[var(--color-error)]">-{formatLOC(c.lines_deleted)}</span>
        </span>
      ),
    },
    {
      key: "risk",
      header: "Review priority",
      headerClassName: "w-36",
      priority: 3,
      // Context, not the lead: the diff-shape rank supports the health delta,
      // so it stays plain text with no colour of its own beside the chip.
      render: (c) => (
        <span className="whitespace-nowrap text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {priorityText(c)}
        </span>
      ),
      mobileRender: priorityText,
    },
  ];
}
