"use client";

import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { formatCost, formatTokens } from "../lib/format";

const PROVIDER_PATTERNS: Array<{ provider: string; match: RegExp; color: string }> = [
  { provider: "Anthropic", match: /^claude/i, color: "#d97757" },
  { provider: "OpenAI", match: /^(gpt|o\d|chatgpt)/i, color: "#10a37f" },
  { provider: "Google", match: /^gemini/i, color: "#4285f4" },
  { provider: "Mistral", match: /^(mistral|mixtral|codestral)/i, color: "#fa520f" },
  { provider: "DeepSeek", match: /^deepseek/i, color: "#4d6bfe" },
  { provider: "Groq", match: /^(groq|llama)/i, color: "#f55036" },
  { provider: "Other", match: /.*/, color: "var(--color-lang-other)" },
];

function classifyProvider(model: string): { provider: string; color: string } {
  for (const p of PROVIDER_PATTERNS) {
    if (p.match.test(model)) return { provider: p.provider, color: p.color };
  }
  return { provider: "Other", color: "var(--color-lang-other)" };
}

export interface ProviderComparisonProps {
  modelGroups: Array<{
    group: string; // model name
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }>;
}

export function ProviderComparison({ modelGroups }: ProviderComparisonProps) {
  const byProvider = new Map<
    string,
    { provider: string; color: string; calls: number; cost_usd: number; tokens: number; models: string[] }
  >();
  for (const m of modelGroups) {
    const { provider, color } = classifyProvider(m.group);
    const cur =
      byProvider.get(provider) ??
      { provider, color, calls: 0, cost_usd: 0, tokens: 0, models: [] };
    cur.calls += m.calls;
    cur.cost_usd += m.cost_usd;
    cur.tokens += m.input_tokens + m.output_tokens;
    cur.models.push(m.group);
    byProvider.set(provider, cur);
  }

  const rows = Array.from(byProvider.values()).sort((a, b) => b.cost_usd - a.cost_usd);
  const maxCost = rows.reduce((m, r) => Math.max(m, r.cost_usd), 0) || 1;

  if (rows.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-secondary)] py-8 text-center">
        No model data yet.
      </p>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Spend by provider</CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-3">
        {rows.map((r) => (
          <div key={r.provider}>
            <div className="flex items-baseline justify-between text-xs">
              <div className="flex items-center gap-2 min-w-0">
                <span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: r.color }} />
                <span className="font-medium text-[var(--color-text-primary)]">{r.provider}</span>
                <span className="text-[var(--color-text-tertiary)] truncate">
                  · {r.models.length} model{r.models.length === 1 ? "" : "s"}
                </span>
              </div>
              <span className="tabular-nums text-[var(--color-text-secondary)] ml-2 shrink-0">
                {formatCost(r.cost_usd)}
              </span>
            </div>
            <div className="mt-1 h-2 w-full rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
              <div
                className="h-full rounded-full transition-all"
                style={{ width: `${(r.cost_usd / maxCost) * 100}%`, backgroundColor: r.color }}
              />
            </div>
            <div className="mt-0.5 flex items-center justify-between text-[10px] text-[var(--color-text-tertiary)] tabular-nums">
              <span>{r.calls.toLocaleString()} calls</span>
              <span>{formatTokens(r.tokens)} tokens</span>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
