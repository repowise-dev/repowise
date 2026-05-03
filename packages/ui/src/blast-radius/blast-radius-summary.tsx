import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

interface BlastRadiusSummaryProps {
  result: BlastRadiusResponse;
}

/** Four-stat summary card sitting beside the RiskScoreCard. */
export function BlastRadiusSummary({ result }: BlastRadiusSummaryProps) {
  const stats = [
    { label: "Direct Risks", value: result.direct_risks.length },
    { label: "Transitive Files", value: result.transitive_affected.length },
    { label: "Co-change Warnings", value: result.cochange_warnings.length },
    { label: "Test Gaps", value: result.test_gaps.length },
  ];
  return (
    <Card className="sm:col-span-3">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">Summary</CardTitle>
      </CardHeader>
      <CardContent className="pt-0 grid grid-cols-2 sm:grid-cols-4 gap-4">
        {stats.map(({ label, value }) => (
          <div key={label} className="space-y-1">
            <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
              {value}
            </p>
            <p className="text-xs text-[var(--color-text-tertiary)]">{label}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
