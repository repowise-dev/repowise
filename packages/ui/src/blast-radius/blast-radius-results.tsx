import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { RiskScoreCard } from "./risk-score-card";
import { BlastRadiusSummary } from "./blast-radius-summary";
import { TableSection } from "./table-section";
import { DirectRisksTable } from "./direct-risks-table";
import { TransitiveTable } from "./transitive-table";
import { CochangeTable } from "./cochange-table";
import { ReviewersTable } from "./reviewers-table";
import { TestGapsList } from "./test-gaps-list";

interface BlastRadiusResultsProps {
  result: BlastRadiusResponse;
}

/** Composes the full results stack — score gauge, summary, all five tables. */
export function BlastRadiusResults({ result }: BlastRadiusResultsProps) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <RiskScoreCard score={result.overall_risk_score} />
        <BlastRadiusSummary result={result} />
      </div>

      <TableSection title="Direct Risks" empty={result.direct_risks.length === 0}>
        <DirectRisksTable rows={result.direct_risks} />
      </TableSection>

      <TableSection
        title="Transitive Affected Files"
        empty={result.transitive_affected.length === 0}
      >
        <TransitiveTable rows={result.transitive_affected} />
      </TableSection>

      <TableSection
        title="Co-change Warnings"
        empty={result.cochange_warnings.length === 0}
      >
        <CochangeTable rows={result.cochange_warnings} />
      </TableSection>

      <TableSection
        title="Recommended Reviewers"
        empty={result.recommended_reviewers.length === 0}
      >
        <ReviewersTable rows={result.recommended_reviewers} />
      </TableSection>

      <TableSection title="Test Gaps" empty={result.test_gaps.length === 0}>
        <TestGapsList gaps={result.test_gaps} />
      </TableSection>
    </div>
  );
}
