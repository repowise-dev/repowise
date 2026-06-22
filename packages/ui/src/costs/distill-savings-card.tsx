"use client";

import { Scissors, Sparkles, Zap } from "lucide-react";
import { Card, CardContent } from "../ui/card";
import { formatCost, formatTokens } from "../lib/format";

export interface DistillSavingsGroup {
  group: string;
  events: number;
  raw_tokens: number;
  distilled_tokens: number;
  saved_tokens: number;
}

export interface McpDropGroup {
  tool: string;
  events: number;
  tokens: number;
  /** "counterfactual" (answer replaced raw exploration) or "truncation" (budget drop). */
  kind?: string;
}

export interface DistillSavingsData {
  available: boolean;
  events: number;
  raw_tokens: number;
  distilled_tokens: number;
  saved_tokens: number;
  estimated_usd_saved: number;
  pricing_model: string;
  pricing_agent?: string;
  pricing_source?: string;
  per_filter: DistillSavingsGroup[];
  per_day: DistillSavingsGroup[];
  mcp_events?: number;
  mcp_tokens?: number;
  /** Count of counterfactual MCP queries answered ("N MCP queries answered"). */
  mcp_queries?: number;
  mcp_per_tool?: McpDropGroup[];
  /** Raw (non-distilled) agent commands a filter would have caught. */
  missed_events?: number;
  missed_tokens_est?: number;
  missed_window_days?: number;
  /** Full re-reads of unchanged files a targeted get_symbol would have replaced. */
  reread_events?: number;
  reread_tokens_est?: number;
}

export interface DistillSavingsCardProps {
  /** Savings rollup from /distill-savings; undefined while loading. */
  data?: DistillSavingsData;
}

const DISTILL_DOCS = "https://github.com/repowise-dev/repowise/blob/main/docs/DISTILL.md";

/** Humanize the resolved pricing agent for the "priced at" caption. */
function agentLabel(agent: string | undefined): string {
  if (agent === "claude_code") return "Claude Code";
  if (agent === "codex") return "Codex";
  return "";
}

/**
 * Hero results card for the Costs page: every token & dollar repowise saved the
 * coding agent, across the `repowise distill` ledger (CLI + hook) and the MCP
 * tools — each curated answer replacing raw file exploration. Priced at the
 * agent's *real* model — saved tokens are input the agent never had to read.
 */
export function DistillSavingsCard({ data }: DistillSavingsCardProps) {
  const distillSaved = data?.saved_tokens ?? 0;
  const mcpSaved = data?.mcp_tokens ?? 0;
  const total = distillSaved + mcpSaved;
  const hasData = !!data?.available && total > 0;

  if (!data) {
    return (
      <Card>
        <CardContent className="py-10">
          <div className="h-24 w-full animate-pulse rounded-lg bg-[var(--color-bg-inset)]" />
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

  const distillPct = total > 0 ? Math.round((distillSaved / total) * 100) : 0;
  const mcpPct = 100 - distillPct;
  const topFilters = [...data.per_filter]
    .sort((a, b) => b.saved_tokens - a.saved_tokens)
    .slice(0, 5);
  const topTools = (data.mcp_per_tool ?? []).slice(0, 5);
  const priced = agentLabel(data.pricing_agent) || data.pricing_model;
  const detected = data.pricing_source && data.pricing_source !== "default";
  const missed = (data.missed_tokens_est ?? 0) > 0;
  const reread = (data.reread_tokens_est ?? 0) > 0;

  // Prefer the counterfactual framing ("N queries answered") when any MCP call
  // recorded a real saving; fall back to the truncation-drop count otherwise.
  const mcpQueries = data.mcp_queries ?? 0;
  const mcpEvents = data.mcp_events ?? 0;
  const mcpCaption =
    mcpQueries > 0
      ? `MCP · ${mcpQueries.toLocaleString()} quer${mcpQueries === 1 ? "y" : "ies"} answered`
      : `MCP · ${mcpEvents.toLocaleString()} drop${mcpEvents === 1 ? "" : "s"}`;

  return (
    <Card>
      <CardContent className="py-6">
        {/* Headline */}
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
              <Sparkles className="h-3.5 w-3.5 text-[var(--color-savings-distill)]" />
              Tokens saved for your agent
            </div>
            <div className="mt-1 flex items-baseline gap-3">
              <span className="text-4xl font-semibold tabular-nums text-[var(--color-text-primary)]">
                {formatTokens(total)}
              </span>
              <span className="text-2xl font-semibold tabular-nums text-[var(--color-success)]">
                {formatCost(data.estimated_usd_saved)}
              </span>
            </div>
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
              priced at {priced}
              {detected ? (
                <span className="text-[var(--color-text-tertiary)]"> · {data.pricing_source}</span>
              ) : (
                <span
                  className="text-[var(--color-text-tertiary)]"
                  title="No agent session was detected, so savings are valued at the standard published rate for this model."
                >
                  {" "}
                  · estimated at the standard model rate
                </span>
              )}
            </p>
          </div>
          <div className="flex gap-6 text-right">
            <div>
              <div className="text-lg font-semibold tabular-nums text-[var(--color-text-primary)]">
                {formatTokens(distillSaved)}
              </div>
              <div className="text-xs text-[var(--color-text-tertiary)]">
                distill · {data.events.toLocaleString()} event{data.events === 1 ? "" : "s"}
              </div>
            </div>
            <div>
              <div className="text-lg font-semibold tabular-nums text-[var(--color-text-primary)]">
                {formatTokens(mcpSaved)}
              </div>
              <div className="text-xs text-[var(--color-text-tertiary)]">{mcpCaption}</div>
            </div>
          </div>
        </div>

        {/* Two-segment breakdown bar */}
        <div className="mt-4 flex h-2.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
          {distillSaved > 0 && (
            <div
              className="h-full bg-[var(--color-savings-distill)]"
              style={{ width: `${distillPct}%` }}
              title={`Distill — ${formatTokens(distillSaved)} (${distillPct}%)`}
            />
          )}
          {mcpSaved > 0 && (
            <div
              className="h-full bg-[var(--color-savings-mcp)]"
              style={{ width: `${mcpPct}%` }}
              title={`MCP tools — ${formatTokens(mcpSaved)} (${mcpPct}%)`}
            />
          )}
        </div>
        <div className="mt-1.5 flex items-center gap-4 text-xs text-[var(--color-text-secondary)]">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-[var(--color-savings-distill)]" /> Distill {distillPct}%
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-[var(--color-savings-mcp)]" /> MCP tools {mcpPct}%
          </span>
        </div>

        {/* Per-surface detail */}
        <div className="mt-4 grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2">
          {topFilters.length > 0 && (
            <SurfaceDetail
              title="Distill — by filter"
              rows={topFilters.map((f) => ({
                label: f.group,
                tokens: f.saved_tokens,
              }))}
              max={distillSaved}
              barClass="bg-[var(--color-savings-distill)]"
            />
          )}
          {topTools.length > 0 && (
            <SurfaceDetail
              title="MCP — by tool"
              rows={topTools.map((t) => ({ label: t.tool, tokens: t.tokens }))}
              max={mcpSaved}
              barClass="bg-[var(--color-savings-mcp)]"
            />
          )}
        </div>

        {/* Missed → opportunity CTA */}
        {missed && (
          <a
            href={DISTILL_DOCS}
            target="_blank"
            rel="noreferrer"
            className="mt-5 flex items-center gap-3 rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 px-3 py-2.5 transition-colors hover:bg-[var(--color-warning)]/10"
          >
            <Zap className="h-4 w-4 shrink-0 text-[var(--color-warning)]" />
            <div className="text-xs text-[var(--color-text-secondary)]">
              <span className="font-medium text-[var(--color-warning)]">
                Unlock ~{formatTokens(data.missed_tokens_est ?? 0)} more
              </span>{" "}
              — {(data.missed_events ?? 0).toLocaleString()} raw command
              {(data.missed_events ?? 0) === 1 ? "" : "s"} bypassed distill in the last{" "}
              {data.missed_window_days ?? 7} days. Enable auto-capture →
            </div>
          </a>
        )}

        {/* Re-read waste → MCP opportunity CTA */}
        {reread && (
          <div className="mt-3 flex items-center gap-3 rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 px-3 py-2.5">
            <Zap className="h-4 w-4 shrink-0 text-[var(--color-warning)]" />
            <div className="text-xs text-[var(--color-text-secondary)]">
              <span className="font-medium text-[var(--color-warning)]">
                Save ~{formatTokens(data.reread_tokens_est ?? 0)} more
              </span>{" "}
              — {(data.reread_events ?? 0).toLocaleString()} full re-read
              {(data.reread_events ?? 0) === 1 ? "" : "s"} of unchanged files a targeted{" "}
              <code>get_symbol</code> would have replaced.
            </div>
          </div>
        )}

        <p className="mt-3 text-xs leading-snug text-[var(--color-text-tertiary)]">
          Distill counts <code>repowise distill</code> command/hook savings; MCP counts the raw
          file exploration each tool answer replaced (plus any over-budget content trimmed). Saved
          tokens are agent input, priced at the agent&apos;s input rate. Everything stays on this
          machine.
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
