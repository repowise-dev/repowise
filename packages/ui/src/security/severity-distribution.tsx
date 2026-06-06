"use client";

import { ShieldAlert } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

const SEVERITY_ORDER = ["high", "med", "low"] as const;
const SEVERITY_LABELS: Record<string, string> = {
  high: "High / critical",
  med: "Medium",
  low: "Low",
};
const SEVERITY_COLORS: Record<string, string> = {
  high: "var(--color-error)",
  med: "var(--color-warning)",
  low: "var(--color-success)",
};

export interface SeverityDistributionProps {
  /** Map of severity → count. */
  counts: Record<string, number>;
}

export function SeverityDistribution({ counts }: SeverityDistributionProps) {
  const total = Object.values(counts).reduce((s, n) => s + n, 0);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-red-400" />
          Severity distribution
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-3">
        {total === 0 ? (
          <p className="text-xs text-[var(--color-text-secondary)] py-4 text-center">
            No security findings.
          </p>
        ) : (
          <>
            <div className="flex h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
              {SEVERITY_ORDER.map((sev) => {
                const c = counts[sev] ?? 0;
                if (c === 0) return null;
                return (
                  <div
                    key={sev}
                    style={{
                      width: `${(c / total) * 100}%`,
                      backgroundColor: SEVERITY_COLORS[sev],
                    }}
                    title={`${SEVERITY_LABELS[sev]}: ${c}`}
                  />
                );
              })}
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs">
              {SEVERITY_ORDER.map((sev) => {
                const c = counts[sev] ?? 0;
                return (
                  <div
                    key={sev}
                    className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2"
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{ backgroundColor: SEVERITY_COLORS[sev] }}
                      />
                      <span className="text-[var(--color-text-secondary)]">{SEVERITY_LABELS[sev]}</span>
                    </div>
                    <p className="text-lg font-semibold text-[var(--color-text-primary)] tabular-nums">
                      {c}
                    </p>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
