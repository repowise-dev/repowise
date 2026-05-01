import type { DirectRiskEntry } from "@repowise-dev/types/blast-radius";
import { Th, Td } from "./cells";

interface DirectRisksTableProps {
  rows: DirectRiskEntry[];
}

export function DirectRisksTable({ rows }: DirectRisksTableProps) {
  // Backend risk_score / temporal_hotspot are 0–1; render 0–10 to match the gauge.
  const fmt10 = (v: number) => (v * 10).toFixed(2);
  const fmtPct = (v: number) => `${(v * 100).toFixed(2)}%`;
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <caption className="sr-only">Direct risks for the changed files</caption>
        <thead>
          <tr>
            <Th>File</Th>
            <Th>Risk Score</Th>
            <Th>Temporal Hotspot</Th>
            <Th>Centrality</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.path} className="border-t border-[var(--color-border-default)]">
              <Td>
                <span className="font-mono break-all" title={r.path}>
                  {r.path}
                </span>
              </Td>
              <Td className="text-right tabular-nums">{fmt10(r.risk_score)}</Td>
              <Td className="text-right tabular-nums">{fmt10(r.temporal_hotspot)}</Td>
              <Td className="text-right tabular-nums">{fmtPct(r.centrality)}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
