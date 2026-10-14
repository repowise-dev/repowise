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

/** Commits by attribution label, as `/commits/agent-trend` reports them.
 * Unknown commits are their own band and are never shown as human. */
export interface StripProvenance {
  agentCommits: number;
  humanAttestedCommits: number;
  unknownCommits: number;
  totalCommits: number;
  /** Agent share of commits with a known source, 0–100; null below the
   * coverage floor. */
  agentPctOfCovered: number | null;
  /** Commits with a known source / all commits, 0–100. */
  coveragePct: number | null;
  /** Per-agent commit counts, biggest first. */
  agentNames: { name: string; count: number }[];
}

interface ContributorsStripProps {
  owners: StripOwner[];
  /** Approx number of distinct contributors (defaults to owners.length). */
  contributorCount?: number;
  /** Attribution by label; omit/null when attribution is off or not indexed. */
  provenance?: StripProvenance | null;
  ownersHref: string;
  commitsHref: string;
}

function authorshipSegments(p: StripProvenance) {
  const share = (n: number) => (n / p.totalCommits) * 100;
  const shown = p.agentNames.slice(0, 3);
  const rest = p.agentNames.slice(3).reduce((sum, a) => sum + a.count, 0);
  return [
    ...shown.map((a) => ({ key: a.name, label: a.name, value: share(a.count) })),
    { key: "__other_agents", label: "Other agents", value: share(rest) },
    { key: "__human_attested", label: "Human-attested", value: share(p.humanAttestedCommits) },
    { key: "__unknown", label: "Unknown", value: share(p.unknownCommits), tail: true },
  ];
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
                  {provenance!.agentPctOfCovered != null && (
                    <>
                      at least{" "}
                      <span className="font-semibold text-[var(--color-accent-primary)]">
                        {Math.round(provenance!.agentPctOfCovered)}%
                      </span>{" "}
                      of covered,{" "}
                    </>
                  )}
                  coverage {Math.round(provenance!.coveragePct ?? 0)}%
                </a>
                <InfoTip content={AGENT_PCT_HINT} label="How agent authorship is measured" />
              </span>
            </div>
            <ProportionBar
              label="Commits by author"
              size="sm"
              // Agents first, then the known human band, then unknown, in
              // that fixed order so unknown never folds into another band.
              sort={false}
              segments={authorshipSegments(provenance!)}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
