import { Users, Bot, ArrowRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

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

/* Categorical hues that read in both themes (community ramp is built for
   exactly this — distinguishable, brand-anchored). */
const SEG_COLORS = [
  "var(--color-community-1)",
  "var(--color-community-2)",
  "var(--color-community-3)",
  "var(--color-community-6)",
  "var(--color-community-7)",
];
const OTHER_COLOR = "var(--color-text-tertiary)";

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
        {/* Ownership share */}
        <div className="flex h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
          {top.map((o, i) => (
            <div
              key={o.email ?? o.name}
              className="h-full"
              style={{ width: `${o.pct}%`, background: SEG_COLORS[i % SEG_COLORS.length] }}
              title={`${o.name} — ${Math.round(o.pct)}% of files`}
            />
          ))}
          {otherPct > 1 && (
            <div className="h-full" style={{ width: `${otherPct}%`, background: OTHER_COLOR }} />
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {top.slice(0, 4).map((o, i) => (
            <span
              key={o.email ?? o.name}
              className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)] min-w-0"
            >
              <span
                className="h-2 w-2 rounded-full shrink-0"
                style={{ background: SEG_COLORS[i % SEG_COLORS.length] }}
              />
              <span className="truncate max-w-[120px]">{firstName(o.name)}</span>
              <span className="tabular-nums text-[var(--color-text-tertiary)]">
                {Math.round(o.pct)}%
              </span>
            </span>
          ))}
          {owners.length > 4 && (
            <span className="text-xs text-[var(--color-text-tertiary)]">
              +{owners.length - 4} more
            </span>
          )}
        </div>
        {/* Agent provenance — only when there's something to report */}
        {hasProvenance && (
          <div className="mt-1 border-t border-[var(--color-border-default)] pt-2.5 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                <Bot className="h-3 w-3" />
                Authorship
              </span>
              <a
                href={commitsHref}
                className="text-xs tabular-nums text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] transition-colors"
              >
                <span className="font-semibold text-[var(--color-accent-primary)]">
                  {Math.round(provenance!.agentPct)}%
                </span>{" "}
                agent-written
              </a>
            </div>
            <div className="flex h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
              <div
                className="h-full"
                style={{ width: `${100 - provenance!.agentPct}%`, background: "var(--color-text-tertiary)" }}
                title={`Human — ${Math.round(100 - provenance!.agentPct)}%`}
              />
              <div
                className="h-full"
                style={{ width: `${provenance!.agentPct}%`, background: "var(--color-accent-fill)" }}
                title={`Agents — ${Math.round(provenance!.agentPct)}%`}
              />
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-secondary)]">
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-[var(--color-text-tertiary)]" />
                Human
                <span className="tabular-nums text-[var(--color-text-tertiary)]">
                  {Math.round(100 - provenance!.agentPct)}%
                </span>
              </span>
              {provenance!.agentNames.slice(0, 2).map((a) => {
                const pct = Math.round((a.count / provenance!.totalCommits) * 100);
                return (
                  <span key={a.name} className="flex items-center gap-1.5 min-w-0">
                    <span className="h-2 w-2 rounded-full bg-[var(--color-accent-fill)]" />
                    <span className="truncate max-w-[120px]">{a.name}</span>
                    <span className="tabular-nums text-[var(--color-text-tertiary)]">{pct}%</span>
                  </span>
                );
              })}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
