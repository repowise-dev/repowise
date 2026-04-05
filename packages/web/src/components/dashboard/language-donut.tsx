"use client";

import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Code } from "lucide-react";

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

interface LanguageDonutProps {
  /** Map of language → file count */
  distribution: Record<string, number>;
}

export function LanguageDonut({ distribution }: LanguageDonutProps) {
  const entries = Object.entries(distribution)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value);

  const total = entries.reduce((s, e) => s + e.value, 0);
  if (total === 0) return null;

  // Show top 6, group rest as "other"
  const shown = entries.slice(0, 6);
  const otherValue = entries.slice(6).reduce((s, e) => s + e.value, 0);
  if (otherValue > 0) shown.push({ name: "other", value: otherValue });

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Code className="h-4 w-4 text-[var(--color-text-secondary)]" />
          Languages
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex items-center gap-4">
          <div className="w-[120px] h-[120px] shrink-0">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={shown}
                  cx="50%"
                  cy="50%"
                  innerRadius={32}
                  outerRadius={55}
                  paddingAngle={2}
                  dataKey="value"
                  stroke="none"
                >
                  {shown.map((entry) => (
                    <Cell key={entry.name} fill={getLangColor(entry.name)} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: "var(--color-bg-overlay)",
                    border: "1px solid var(--color-border-default)",
                    borderRadius: 8,
                    fontSize: 11,
                    color: "var(--color-text-primary)",
                  }}
                  formatter={(value: number) => [`${value} files (${Math.round((value / total) * 100)}%)`, ""]}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="space-y-1.5 min-w-0 flex-1">
            {shown.map((entry) => (
              <div key={entry.name} className="flex items-center justify-between text-[11px]">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span
                    className="h-2 w-2 rounded-full shrink-0"
                    style={{ backgroundColor: getLangColor(entry.name) }}
                  />
                  <span className="text-[var(--color-text-secondary)] capitalize truncate">
                    {entry.name}
                  </span>
                </div>
                <span className="text-[var(--color-text-tertiary)] tabular-nums ml-2 shrink-0">
                  {Math.round((entry.value / total) * 100)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

