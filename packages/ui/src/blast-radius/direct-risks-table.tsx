import type { DirectRiskEntry } from "@repowise-dev/types/blast-radius";

interface DirectRisksTableProps {
  rows: DirectRiskEntry[];
}

/** Health-band ink, matching the impact-graph node colours. */
function riskInk(risk01: number): string {
  if (risk01 >= 0.66) return "var(--color-error)";
  if (risk01 >= 0.33) return "var(--color-warning)";
  return "var(--color-success)";
}

/** A 0–1 value rendered as a labelled mini-bar so rows scan visually. */
function MiniBar({
  value01,
  color,
  display,
}: {
  value01: number;
  color: string;
  display: string;
}) {
  const pct = Math.max(0, Math.min(100, value01 * 100));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-full min-w-[48px] overflow-hidden rounded-full bg-[var(--color-bg-wash)]">
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="w-10 shrink-0 text-right tabular-nums text-[var(--color-text-secondary)]">
        {display}
      </span>
    </div>
  );
}

/**
 * Direct dependents of the changed files, sorted by risk. Each numeric column
 * is an inline mini-bar (risk health-banded, hotspot/centrality neutral) so the
 * heaviest rows pop without reading every figure.
 */
export function DirectRisksTable({ rows }: DirectRisksTableProps) {
  const sorted = [...rows].sort((a, b) => b.risk_score - a.risk_score);
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <caption className="sr-only">Direct risks for the changed files</caption>
        <thead>
          <tr className="text-left text-xs font-medium text-[var(--color-text-tertiary)]">
            <th className="py-1.5 pr-4">File</th>
            <th className="w-[28%] py-1.5 pr-4">Risk (0–10)</th>
            <th className="w-[24%] py-1.5 pr-4">Temporal hotspot</th>
            <th className="w-[24%] py-1.5">Centrality</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.path} className="border-t border-[var(--color-border-default)]">
              <td className="py-2 pr-4 align-middle">
                <span
                  className="block max-w-[280px] truncate font-mono text-xs text-[var(--color-text-secondary)]"
                  title={r.path}
                >
                  {r.path}
                </span>
              </td>
              <td className="py-2 pr-4 align-middle">
                <MiniBar
                  value01={r.risk_score}
                  color={riskInk(r.risk_score)}
                  display={(r.risk_score * 10).toFixed(1)}
                />
              </td>
              <td className="py-2 pr-4 align-middle">
                <MiniBar
                  value01={r.temporal_hotspot}
                  color="var(--color-accent-secondary)"
                  display={(r.temporal_hotspot * 10).toFixed(1)}
                />
              </td>
              <td className="py-2 align-middle">
                <MiniBar
                  value01={r.centrality}
                  color="var(--color-info)"
                  display={`${(r.centrality * 100).toFixed(0)}%`}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
