import { Sparkles } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { formatCost, formatTokens } from "../lib/format";

/** Structural slice of the savings rollup this tile renders — the full
 *  /distill-savings payload and the overview-summary headline both fit. */
export interface SavingsMiniData {
  available: boolean;
  saved_tokens?: number;
  mcp_tokens?: number;
  estimated_usd_saved?: number;
  pricing_model?: string;
}

const LANG_COLORS: Record<string, string> = {
  python: "var(--color-lang-python)",
  typescript: "var(--color-lang-typescript)",
  javascript: "var(--color-lang-typescript)",
  go: "var(--color-lang-go)",
  rust: "var(--color-lang-rust)",
  java: "var(--color-lang-java)",
  cpp: "var(--color-lang-cpp)",
  c: "var(--color-lang-cpp)",
  config: "var(--color-lang-config)",
};

function getLangColor(lang: string): string {
  return LANG_COLORS[lang.toLowerCase()] ?? "var(--color-lang-other)";
}

interface SavingsMiniProps {
  /** Rollup from /distill-savings (or the overview-summary headline);
   *  null/undefined when unavailable. */
  data?: SavingsMiniData | null;
  /** Repo id, for the "View costs →" link. */
  repoId: string;
  /** Language → file-count map, rendered as a composition strip below savings. */
  langDistribution?: Record<string, number>;
  /** Optional "View in Graph →" target for the languages section. */
  langHref?: string;
}

/**
 * Compact overview tile for agent token savings — the headline number, dollar
 * value priced at the detected agent model, and a distill-vs-MCP split, with a
 * jump to the full Costs results page. A language-composition strip rides along
 * the bottom of the same card.
 */
export function SavingsMini({ data, repoId, langDistribution, langHref }: SavingsMiniProps) {
  const distillSaved = data?.saved_tokens ?? 0;
  const mcpSaved = data?.mcp_tokens ?? 0;
  const total = distillSaved + mcpSaved;
  const hasData = !!data?.available && total > 0;
  const costsHref = `/repos/${repoId}/costs`;

  const distillPct = total > 0 ? Math.round((distillSaved / total) * 100) : 0;
  const mcpPct = 100 - distillPct;

  // Languages composition strip.
  const langEntries = Object.entries(langDistribution ?? {})
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value);
  const langTotal = langEntries.reduce((s, e) => s + e.value, 0);
  const langShown = langEntries.slice(0, 6);
  const langOther = langEntries.slice(6).reduce((s, e) => s + e.value, 0);
  if (langOther > 0) langShown.push({ name: "other", value: langOther });

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-cyan-500" />
            Agent savings
          </span>
          <a
            href={costsHref}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline font-normal"
          >
            View costs →
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {hasData ? (
          <div className="space-y-3">
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-semibold tabular-nums text-[var(--color-text-primary)]">
                {formatTokens(total)}
              </span>
              <span className="text-base font-semibold tabular-nums text-green-500">
                {formatCost(data!.estimated_usd_saved ?? 0)}
              </span>
            </div>
            <p className="text-[11px] text-[var(--color-text-secondary)] -mt-1">
              tokens saved for your agent
              {data!.pricing_model ? (
                <span className="text-[var(--color-text-tertiary)]">
                  {" "}
                  · priced at {data!.pricing_model}
                </span>
              ) : null}
            </p>

            <div className="flex h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
              {distillSaved > 0 && (
                <div
                  className="h-full bg-cyan-500"
                  style={{ width: `${distillPct}%` }}
                  title={`Distill — ${formatTokens(distillSaved)}`}
                />
              )}
              {mcpSaved > 0 && (
                <div
                  className="h-full bg-violet-500"
                  style={{ width: `${mcpPct}%` }}
                  title={`MCP tools — ${formatTokens(mcpSaved)}`}
                />
              )}
            </div>
            <div className="space-y-1 text-[11px]">
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-[var(--color-text-secondary)] whitespace-nowrap">
                  <span className="h-2 w-2 rounded-full bg-cyan-500 shrink-0" /> Distill
                </span>
                <span className="tabular-nums text-[var(--color-text-tertiary)] shrink-0">
                  {formatTokens(distillSaved)}
                </span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-[var(--color-text-secondary)] whitespace-nowrap">
                  <span className="h-2 w-2 rounded-full bg-violet-500 shrink-0" /> MCP tools
                </span>
                <span className="tabular-nums text-[var(--color-text-tertiary)] shrink-0">
                  {formatTokens(mcpSaved)}
                </span>
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-2 py-1">
            <p className="text-xs text-[var(--color-text-secondary)] leading-snug">
              Trim what your agent reads with <code>repowise distill</code> and the MCP tools —
              savings show up here.
            </p>
            <a
              href={costsHref}
              className="inline-block text-[11px] text-[var(--color-accent-primary)] hover:underline"
            >
              Open the Costs page →
            </a>
          </div>
        )}

        {langTotal > 0 && (
          <div className="mt-4 border-t border-[var(--color-border-secondary)] pt-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
                Languages
              </span>
              {langHref && (
                <a
                  href={langHref}
                  className="text-[10px] text-[var(--color-accent-primary)] hover:underline"
                >
                  View in Graph →
                </a>
              )}
            </div>
            <div className="flex h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
              {langShown.map((entry) => (
                <div
                  key={entry.name}
                  className="h-full"
                  style={{
                    width: `${(entry.value / langTotal) * 100}%`,
                    backgroundColor: getLangColor(entry.name),
                  }}
                  title={`${entry.name} — ${Math.round((entry.value / langTotal) * 100)}%`}
                />
              ))}
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1">
              {langShown.map((entry) => (
                <div
                  key={entry.name}
                  className="flex items-center justify-between text-[11px] min-w-0"
                >
                  <span className="flex items-center gap-1.5 min-w-0">
                    <span
                      className="h-2 w-2 rounded-full shrink-0"
                      style={{ backgroundColor: getLangColor(entry.name) }}
                    />
                    <span className="text-[var(--color-text-secondary)] capitalize truncate">
                      {entry.name}
                    </span>
                  </span>
                  <span className="text-[var(--color-text-tertiary)] tabular-nums ml-2 shrink-0">
                    {Math.round((entry.value / langTotal) * 100)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
