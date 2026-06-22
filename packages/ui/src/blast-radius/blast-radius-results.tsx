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
          <EmptyState title="No direct risks" description="Nothing depends directly on the changed files." />
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="Transitive affected files"
        hint={result.transitive_affected.length || undefined}
      >
        {result.transitive_affected.length > 0 ? (
          <TransitiveTable rows={result.transitive_affected} />
        ) : (
          <EmptyState title="No transitive impact" description="No deeper dependents within the selected depth." />
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="Co-change warnings"
        hint={result.cochange_warnings.length || undefined}
      >
        {result.cochange_warnings.length > 0 ? (
          <CochangeTable rows={result.cochange_warnings} />
        ) : (
          <EmptyState title="No co-change warnings" description="No historical co-change partners are missing from this change." />
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
            <EmptyState title="No reviewer suggestions" description="No owners matched the changed files." />
          )}
        </CollapsibleSection>
      )}

      <CollapsibleSection title="Test gaps" hint={result.test_gaps.length || undefined}>
        {result.test_gaps.length > 0 ? (
          <TestGapsList gaps={result.test_gaps} />
        ) : (
          <EmptyState title="No test gaps" description="Affected files have associated tests." />
        )}
      </CollapsibleSection>
    </div>
  );
}
