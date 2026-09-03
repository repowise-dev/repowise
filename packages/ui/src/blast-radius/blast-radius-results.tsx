import type { ReactNode } from "react";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { BlastRadiusHeader } from "./blast-radius-header";
import { ImpactGraph } from "./impact-graph";
import { DirectRisksTable } from "./direct-risks-table";
import { TransitiveTable } from "./transitive-table";
import { CochangeTable } from "./cochange-table";
import { ReviewersTable } from "./reviewers-table";
import { TestGapsList } from "./test-gaps-list";
import { CollapsibleSection } from "../shared/collapsible-section";
import { EmptyState } from "../shared/empty-state";

interface BlastRadiusResultsProps {
  result: BlastRadiusResponse;
  /** The files the user proposed changing — graph centre. */
  changedFiles?: string[];
  /** Rich reviewer panel (e.g. `ReviewerSuggestions` fed by the
   *  reviewer-suggestions endpoint). Replaces the thin email table. */
  reviewersSlot?: ReactNode | undefined;
}

/**
 * Airy blast-radius results: a risk gauge + summary, then a single impact-graph
 * canvas, with the detail tables demoted behind collapsible sections instead of
 * five stacked bordered cards.
 */
export function BlastRadiusResults({
  result,
  changedFiles = [],
  reviewersSlot,
}: BlastRadiusResultsProps) {
  const testImpact = result.test_impact;
  const recommendations = testImpact?.recommendations ?? [];
  const testAnalysisUnavailable =
    !testImpact ||
    ["unavailable", "degraded"].includes(testImpact.coverage.status);
  const testAnalysisLimited =
    testAnalysisUnavailable ||
    Boolean(testImpact?.analysis.partial || testImpact?.analysis.stale);
  const testAnalysisNote = !testImpact
    ? "This older server did not provide typed test-analysis state."
    : testImpact.analysis.stale
      ? "Coverage evidence is stale; measured recommendations may not describe the indexed commit."
      : testImpact.analysis.degraded
        ? "Test analysis is degraded; available recommendations are incomplete."
        : testImpact.analysis.partial
          ? "Test analysis is partial; available recommendations do not cover every evidence input."
          : null;

  return (
    <div className="space-y-6">
      <BlastRadiusHeader result={result} changedFiles={changedFiles} />

      {/* One picture: changed files → direct → transitive. */}
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Impact map
        </p>
        {result.direct_risks.length === 0 &&
        result.transitive_affected.length === 0 ? (
          <EmptyState
            title="No downstream impact found"
            description="No files depend on the changed paths within the selected depth."
          />
        ) : (
          <ImpactGraph result={result} changedFiles={changedFiles} />
        )}
      </div>

      <CollapsibleSection
        title="Direct risks"
        hint={result.direct_risks.length || undefined}
        defaultOpen={result.direct_risks.length > 0}
      >
        {result.direct_risks.length > 0 ? (
          <DirectRisksTable rows={result.direct_risks} />
        ) : (
          <EmptyState
            title="No direct risks"
            description="Nothing depends directly on the changed files."
          />
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="Transitive affected files"
        hint={result.transitive_affected.length || undefined}
      >
        {result.transitive_affected.length > 0 ? (
          <TransitiveTable rows={result.transitive_affected} />
        ) : (
          <EmptyState
            title="No transitive impact"
            description="No deeper dependents within the selected depth."
          />
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="Co-change warnings"
        hint={result.cochange_warnings.length || undefined}
      >
        {result.cochange_warnings.length > 0 ? (
          <CochangeTable rows={result.cochange_warnings} />
        ) : (
          <EmptyState
            title="No co-change warnings"
            description="No historical co-change partners are missing from this change."
          />
        )}
      </CollapsibleSection>

      {reviewersSlot ?? (
        <CollapsibleSection
          title="Recommended reviewers"
          hint={result.recommended_reviewers.length || undefined}
        >
          {result.recommended_reviewers.length > 0 ? (
            <ReviewersTable rows={result.recommended_reviewers} />
          ) : (
            <EmptyState
              title="No reviewer suggestions"
              description="No owners matched the changed files."
            />
          )}
        </CollapsibleSection>
      )}

      <CollapsibleSection
        title="Tests to run"
        hint={testImpact?.recommendations_total || undefined}
        defaultOpen={recommendations.length > 0 || testAnalysisLimited}
      >
        {recommendations.length > 0 ? (
          <div className="space-y-1">
            {testAnalysisNote && (
              <p className="px-2 text-xs text-[var(--color-text-tertiary)]">
                {testAnalysisNote}
              </p>
            )}
            {recommendations.map((recommendation) => (
              <div
                key={`${recommendation.repository_id}:${recommendation.test_id}`}
                className="flex items-start justify-between gap-3 rounded px-2 py-1.5 text-sm"
              >
                <span className="min-w-0 break-all font-mono text-[var(--color-text-primary)]">
                  {recommendation.test_id}
                </span>
                <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
                  {recommendation.basis === "measured"
                    ? "coverage-backed"
                    : "inferred"}
                  {` · ${recommendation.repository}`}
                </span>
              </div>
            ))}
            {testImpact?.recommendations_truncated && (
              <p className="px-2 text-xs text-[var(--color-text-tertiary)]">
                Showing {testImpact.recommendations_emitted} of{" "}
                {testImpact.recommendations_total};{" "}
                {testImpact.recommendations_omitted} omitted.
              </p>
            )}
          </div>
        ) : (
          <EmptyState
            title={
              testAnalysisLimited
                ? "Test analysis limited"
                : "No measured tests found"
            }
            description={
              testAnalysisLimited
                ? `${testAnalysisNote ?? "Test analysis is incomplete."} An empty recommendation list does not mean no tests are needed.`
                : "The available coverage map found no measured tests for this change. This is not proof that no relevant tests exist."
            }
          />
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="Test gaps"
        hint={result.test_gaps.length || undefined}
      >
        {result.test_gaps.length > 0 ? (
          <TestGapsList gaps={result.test_gaps} />
        ) : (
          <EmptyState
            title="No file-level gaps identified"
            description={
              testAnalysisLimited
                ? "Test analysis is limited; this empty list is not evidence that no tests are needed."
                : "No additional file-level gap was identified. Use the typed recommendations and their basis above."
            }
          />
        )}
      </CollapsibleSection>
    </div>
  );
}
