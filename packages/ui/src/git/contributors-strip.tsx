import { Users, Bot, ArrowRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { InfoTip } from "../shared/info-tip";
import { AGENT_PCT_HINT } from "../stats/stat-callout";
import { ProportionBar } from "../shared/proportion-bar";

export interface StripOwner {
  name: string;
  pct: number;
  file_count: number;
  email?: string | undefined;
}

export interface StripProvenance {
  /** Share of indexed commits attributed to coding agents, 0–100. */
  agentPct: number;
  agentCommits: number;
  totalCommits: number;
  /** Per-agent commit counts, biggest first. */
  agentNames: { name: string; count: number }[];
}

interface ContributorsStripProps {
  owners: StripOwner[];
  /** Approx number of distinct contributors (defaults to owners.length). */
  contributorCount?: number;
  /** Agent-vs-human authorship; omit/null when provenance isn't indexed. */
  provenance?: StripProvenance | null;
  ownersHref: string;
  commitsHref: string;
}

function firstName(name: string): string {
  return name.split(/\s+/)[0] ?? name;
}

/**
 * Thin Overview strip: who owns the code (stacked share bar of top authors +
 * legend) and — when agent-provenance is indexed — what share of commits came
 * from coding agents. Degrades to contributors-only when provenance is absent
 * or zero, so repos without agent activity still render cleanly.
 */
export function ContributorsStrip({
  owners,
  contributorCount,
  provenance,
  ownersHref,
  commitsHref,
}: ContributorsStripProps) {
  const top = owners.slice(0, 5);
  const shownPct = top.reduce((s, o) => s + (o.pct ?? 0), 0);
  const otherPct = Math.max(0, 100 - shownPct);
  const count = contributorCount ?? owners.length;

  const hasProvenance =
    !!provenance && provenance.agentCommits > 0 && provenance.totalCommits > 0;

  if (owners.length === 0) return null;

  return (
    <Card className="overflow-hidden shadow-sm">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Users className="h-4 w-4 text-[var(--color-accent-secondary)]" />
            Contributors
            <span className="text-xs font-normal text-[var(--color-text-tertiary)] tabular-nums">
              {count}
            </span>
          </span>
          <a
            href={ownersHref}
            className="inline-flex items-center gap-1 text-xs font-normal text-[var(--color-accent-primary)] hover:underline"
          >
            View owners <ArrowRight className="h-3 w-3" />
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-2.5">
        <ProportionBar
          label="Files by contributor"
          size="sm"
          segments={[
            ...top.map((o) => ({
              key: o.email ?? o.name,
              label: firstName(o.name),
              value: o.pct,
            })),
            { key: "__rest", label: "Others", value: otherPct > 1 ? otherPct : 0, tail: true },
          ]}
        />
        {/* Agent provenance — only when there's something to report */}
        {hasProvenance && (
          <div className="mt-1 border-t border-[var(--color-border-default)] pt-2.5 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                <Bot className="h-3 w-3" />
                Authorship
              </span>
              <span className="flex items-center gap-1">
                <a
                  href={commitsHref}
                  className="text-xs tabular-nums text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] transition-colors"
                >
                  <span className="font-semibold text-[var(--color-accent-primary)]">
                    {Math.round(provenance!.agentPct)}%
                  </span>{" "}
                  agent-written
                </a>
                <InfoTip content={AGENT_PCT_HINT} label="How agent authorship is measured" />
              </span>
            </div>
            <ProportionBar
              label="Commits by author"
              size="sm"
              segments={[
                { key: "human", label: "Human", value: 100 - provenance!.agentPct },
                ...provenance!.agentNames.map((a) => ({
                  key: a.name,
                  label: a.name,
                  value: (a.count / provenance!.totalCommits) * 100,
                })),
              ]}
              maxSegments={3}
              othersLabel={() => "Other agents"}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
