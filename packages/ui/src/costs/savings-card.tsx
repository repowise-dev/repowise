"use client";

import { Scissors, Sparkles, Zap } from "lucide-react";
import { Card, CardContent } from "../ui/card";
import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import { formatCost, formatTokens } from "../lib/format";

export interface SavingsBreakdownRow {
  group: string | null;
  events: number;
  saved_input_tokens: number;
}

export interface SavingsAgentRow {
  agent: string;
  agent_display_name: string | null;
  events: number;
  saved_input_tokens: number;
}

/** The canonical savings report. Field names match the wire exactly, so this
 *  component never has to know how a number was derived — only how to show it. */
export interface SavingsData {
  available: boolean;
  unique_events: number;
  mcp_queries_answered: number;
  saved_input_tokens: number;
  measured_saved_input_tokens: number;
  inferred_saved_input_tokens: number;
  priced_saved_input_tokens: number;
  unpriced_saved_input_tokens: number;
  priced_input_savings_usd: number;
  per_operation: SavingsBreakdownRow[];
  per_surface: SavingsBreakdownRow[];
  /** Carried for callers and for the Phase 4 surface; this card does not
   *  render it, so it is optional rather than a required field nothing reads. */
  per_agent?: SavingsAgentRow[];
  per_day?: SavingsBreakdownRow[];
  missed_events?: number;
  missed_tokens_est?: number;
  missed_window_days?: number;
  reread_events?: number;
  reread_tokens_est?: number;
}

export interface SavingsCardProps {
  /** The savings report; undefined while loading. */
  data?: SavingsData;
}

const DISTILL_DOCS = "https://github.com/repowise-dev/repowise/blob/main/docs/agent/DISTILL.md";

/** Surface slugs read as identifiers; these are the words for them. Kept here
 *  rather than server-side because a surface is a fixed part of the accounting
 *  contract, unlike an agent, whose label comes from the registry on the wire. */
const SURFACE_LABELS: Record<string, string> = {
  distill: "Distill",
  hook: "Hooks",
  mcp: "MCP",
  vscode_lm: "VS Code",
};

function surfaceLabel(slug: string | null): string {
  if (!slug) return "Unknown";
  return SURFACE_LABELS[slug] ?? slug;
}

/**
 * Hero results card for the Costs page: the input tokens repowise kept out of
 * the agent's context, and what they were worth.
 *
 * The headline is deliberately not one confident number. Measured reductions
 * (a known before and after) and inferred avoidance (a documented
 * counterfactual) are different evidence and are shown as such, and the dollar
 * figure covers only the tokens whose events carried a rate.
 */
export function SavingsCard({ data }: SavingsCardProps) {
  const total = data?.saved_input_tokens ?? 0;
  const hasData = !!data?.available && total > 0;

  if (!data) {
    // Mirrors the populated card rather than reserving a flat block: headline,
    // the right-aligned totals, the evidence bar with its legend, and the two
    // detail columns. A flat version stood 176px against a card measuring
    // 350-450px, so the whole page jumped on first paint.
    return (
      <Card>
        <CardContent className="py-6">
          <SkeletonRegion label="Loading agent token savings">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-xs uppercase tracking-wide">
                  <Skeleton className="block h-[1lh] w-48" />
                </div>
                <div className="mt-1 flex items-baseline gap-3">
                  <span className="text-4xl font-semibold">
                    <Skeleton className="block h-[1lh] w-32" />
                  </span>
                  <span className="text-2xl font-semibold">
                    <Skeleton className="block h-[1lh] w-24" />
                  </span>
                </div>
                {/* A div, not a p: Skeleton renders a div, and a p may not
                    contain one. */}
                <div className="mt-1 text-xs">
                  <Skeleton className="block h-[1lh] w-56" />
                </div>
              </div>
              <div className="flex gap-6">
                {[0, 1].map((i) => (
                  <div key={i}>
                    <div className="text-lg font-semibold">
                      <Skeleton className="block h-[1lh] w-20" />
                    </div>
                    <div className="text-xs">
                      <Skeleton className="mt-0.5 block h-[1lh] w-28" />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <Skeleton className="mt-4 h-2.5 w-full rounded-full" />
            <div className="mt-1.5 flex items-center gap-4 text-xs">
              <Skeleton className="block h-[1lh] w-24" />
              <Skeleton className="block h-[1lh] w-24" />
            </div>

            <div className="mt-4 grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2">
              {[0, 1].map((col) => (
                <div key={col} className="space-y-2">
                  <div className="text-xs">
                    <Skeleton className="block h-[1lh] w-32" />
                  </div>
                  {Array.from({ length: 5 }).map((_, row) => (
                    <Skeleton key={row} className="h-4 w-full" />
                  ))}
                </div>
              ))}
            </div>
          </SkeletonRegion>
        </CardContent>
      </Card>
    );
  }

  if (!hasData) {
    return (
      <Card>
        <CardContent className="py-8">
          <div className="flex items-center gap-3">
            <Scissors className="h-5 w-5 shrink-0 text-[var(--color-savings-distill)]" />
            <div>
              <p className="text-sm font-medium text-[var(--color-text-primary)]">
                No agent token savings recorded yet
              </p>
              <p className="mt-1 text-xs text-[var(--color-text-secondary)] max-w-[420px]">
                Route noisy commands through <code>repowise distill &lt;cmd&gt;</code> or install the
                rewrite hook (<code>repowise hook rewrite install</code>) to start trimming agent
                context — savings show up here automatically.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  const measured = data.measured_saved_input_tokens;
  const inferred = data.inferred_saved_input_tokens;
  const measuredPct = total > 0 ? Math.round((measured / total) * 100) : 0;
  const inferredPct = 100 - measuredPct;
  const topOperations = data.per_operation.slice(0, 5);
  const topSurfaces = data.per_surface.slice(0, 5);
  const missed = (data.missed_tokens_est ?? 0) > 0;
  const reread = (data.reread_tokens_est ?? 0) > 0;
  const unpriced = data.unpriced_saved_input_tokens;
  // "Estimated" while any of the total rests on a counterfactual rather than a
  // measured before and after.
  const headline = inferred > 0 ? "Estimated agent savings" : "Tokens saved for your agent";

  return (
    <Card>
      <CardContent className="py-6">
        {/* Headline */}
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
              <Sparkles className="h-3.5 w-3.5 text-[var(--color-savings-distill)]" />
              {headline}
            </div>
            <div className="mt-1 flex items-baseline gap-3">
              <span className="text-4xl font-semibold tabular-nums text-[var(--color-text-primary)]">
                {formatTokens(total)}
              </span>
              <span className="text-2xl font-semibold tabular-nums text-[var(--color-success)]">
                {formatCost(data.priced_input_savings_usd)}
              </span>
            </div>
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
              {unpriced > 0 ? (
                <span
                  title="Each event is priced at the rate recorded when it happened. Events recorded without a rate are counted but not valued."
                >
                  priced on {formatTokens(data.priced_saved_input_tokens)} of{" "}
                  {formatTokens(total)} tokens
                </span>
              ) : (
                <span>priced at the rate recorded on each event</span>
              )}
            </p>
          </div>
          <div className="flex gap-6 text-right">
            <div>
              <div className="text-lg font-semibold tabular-nums text-[var(--color-text-primary)]">
                {data.unique_events.toLocaleString()}
              </div>
              <div className="text-xs text-[var(--color-text-tertiary)]">
                interaction{data.unique_events === 1 ? "" : "s"}
              </div>
            </div>
            <div>
              <div className="text-lg font-semibold tabular-nums text-[var(--color-text-primary)]">
                {data.mcp_queries_answered.toLocaleString()}
              </div>
              <div className="text-xs text-[var(--color-text-tertiary)]">
                MCP quer{data.mcp_queries_answered === 1 ? "y" : "ies"} answered
              </div>
            </div>
          </div>
        </div>

        {/* Evidence mix — the distinction the headline must not flatten. */}
        <div className="mt-4 flex h-2.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
          {measured > 0 && (
            <div
              className="h-full bg-[var(--color-savings-distill)]"
              style={{ width: `${measuredPct}%` }}
              title={`Measured — ${formatTokens(measured)} (${measuredPct}%)`}
            />
          )}
          {inferred > 0 && (
            <div
              className="h-full bg-[var(--color-savings-mcp)]"
              style={{ width: `${inferredPct}%` }}
              title={`Inferred — ${formatTokens(inferred)} (${inferredPct}%)`}
            />
          )}
        </div>
        <div className="mt-1.5 flex items-center gap-4 text-xs text-[var(--color-text-secondary)]">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-[var(--color-savings-distill)]" /> Measured{" "}
            {formatTokens(measured)}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-[var(--color-savings-mcp)]" /> Inferred{" "}
            {formatTokens(inferred)}
          </span>
        </div>

        {/* Where the savings came from */}
        <div className="mt-4 grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2">
          {topSurfaces.length > 0 && (
            <SurfaceDetail
              title="By surface"
              rows={topSurfaces.map((row) => ({
                label: surfaceLabel(row.group),
                tokens: row.saved_input_tokens,
              }))}
              max={total}
              barClass="bg-[var(--color-savings-distill)]"
            />
          )}
          {topOperations.length > 0 && (
            <SurfaceDetail
              title="By operation"
              rows={topOperations.map((row) => ({
                label: row.group ?? "Unknown",
                tokens: row.saved_input_tokens,
              }))}
              max={total}
              barClass="bg-[var(--color-savings-mcp)]"
            />
          )}
        </div>

        {/* Observed opportunities — never part of the total above. */}
        {missed && (
          <div className="mt-5 flex items-center gap-3 rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 px-3 py-2.5">
            <Zap className="h-4 w-4 shrink-0 text-[var(--color-warning)]" />
            <div className="text-xs text-[var(--color-text-secondary)]">
              <span className="font-medium text-[var(--color-warning)]">
                Observed opportunity: ~{formatTokens(data.missed_tokens_est ?? 0)}
              </span>{" "}
              — {(data.missed_events ?? 0).toLocaleString()} raw command
              {(data.missed_events ?? 0) === 1 ? "" : "s"} bypassed distillation in the last{" "}
              {data.missed_window_days ?? 7} days.{" "}
              <a
                href={DISTILL_DOCS}
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2 hover:text-[var(--color-text-primary)]"
              >
                See the distillation setup guide
              </a>
            </div>
          </div>
        )}

        {reread && (
          <div className="mt-3 flex items-center gap-3 rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 px-3 py-2.5">
            <Zap className="h-4 w-4 shrink-0 text-[var(--color-warning)]" />
            <div className="text-xs text-[var(--color-text-secondary)]">
              <span className="font-medium text-[var(--color-warning)]">
                Potentially avoid ~{formatTokens(data.reread_tokens_est ?? 0)}
              </span>{" "}
              — {(data.reread_events ?? 0).toLocaleString()} full re-read
              {(data.reread_events ?? 0) === 1 ? "" : "s"} of unchanged files{" "}
              {(data.reread_events ?? 0) === 1 ? "was" : "were"} observed.
            </div>
          </div>
        )}

        <p className="mt-3 text-xs leading-snug text-[var(--color-text-tertiary)]">
          Savings are input tokens your agent never had to read, recorded one event per
          interaction across the <code>repowise distill</code> path, the replacement hooks and
          MCP calls. Measured figures compare a known before and after; inferred figures estimate
          the exploration an answer replaced. Each event is valued at the rate recorded when it
          happened, so the dollar figure covers only priced events. Counts are estimates
          (~chars/4) and deliberately undersell. Everything stays on this machine.
        </p>
      </CardContent>
    </Card>
  );
}

interface SurfaceDetailRow {
  label: string;
  tokens: number;
}

function SurfaceDetail({
  title,
  rows,
  max,
  barClass,
}: {
  title: string;
  rows: SurfaceDetailRow[];
  max: number;
  barClass: string;
}) {
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
        {title}
      </div>
      {rows.map((row) => {
        const width = max > 0 ? Math.max(4, Math.round((row.tokens / max) * 100)) : 0;
        return (
          <div key={row.label} className="flex items-center gap-2 text-xs">
            <span className="w-28 shrink-0 truncate text-[var(--color-text-secondary)]">
              {row.label}
            </span>
            <div className="h-1.5 flex-1 overflow-hidden rounded bg-[var(--color-bg-inset)]">
              <div className={`h-full rounded ${barClass}`} style={{ width: `${width}%` }} />
            </div>
            <span className="w-14 shrink-0 text-right tabular-nums text-[var(--color-text-primary)]">
              {formatTokens(row.tokens)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
