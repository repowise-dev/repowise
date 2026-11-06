"use client";

import type {
  PerformanceOpportunity,
  PerformanceOpportunityPlanStep,
} from "@repowise-dev/types/health";
import type { StepVerify } from "@repowise-dev/types/refactoring";

import { CommandLine } from "../../../shared/command-line";
import type { PerformanceViewAdapter } from "../adapter";
import { planMatches, usePlan } from "../cache";
import { fixStrategyLabel, planPresentation } from "../presentation";
import { OpportunityTags } from "../tags";
import { Section } from "./layout";

/**
 * Fetch the stored plan and decide whether it names this exact opportunity.
 * A reindexed or reused row must never be handed over as this one's fix, so
 * both the link and the agent handoff wait on the same verdict.
 */
export function useVerifiedPlan(
  opportunity: PerformanceOpportunity | null,
  adapter: PerformanceViewAdapter,
  enabled: boolean,
) {
  const planId =
    opportunity && planPresentation(opportunity).actionable ? opportunity.plan_id : null;
  const { data, error, isLoading } = usePlan(adapter, enabled ? planId : null);
  const matches = Boolean(opportunity && planMatches(data, opportunity));
  return { planId, data, error, isLoading, matches };
}

/**
 * The plan's edits in order; only the rarer mechanical ones are marked.
 *
 * Consecutive steps that make the same edit at different call sites read as
 * one step with its locations: the server emits one step per site, and eight
 * copies of "Collect the keys before the loop" said nothing seven times.
 */
/** Consecutive steps making the same edit, as one group in order. */
export function groupPlanSteps(
  steps: PerformanceOpportunityPlanStep[],
): PerformanceOpportunityPlanStep[][] {
  const groups: PerformanceOpportunityPlanStep[][] = [];
  for (const step of [...steps].sort((a, b) => a.order - b.order)) {
    const last = groups[groups.length - 1];
    if (last && last[0]!.action === step.action && last[0]!.applicability === step.applicability) {
      last.push(step);
    } else {
      groups.push([step]);
    }
  }
  return groups;
}

interface StepPlace {
  place: string;
  verify: StepVerify | undefined;
}

function placesOf(group: PerformanceOpportunityPlanStep[]): StepPlace[] {
  return group.flatMap((step) =>
    step.file_path
      ? [{ place: `${step.file_path}${step.line ? `:${step.line}` : ""}`, verify: step.verify }]
      : [],
  );
}

/** Where one step's edits go: inline for one place, a list for several. */
function StepPlaces({ places }: { places: StepPlace[] }) {
  if (places.length === 1) {
    return (
      <>
        <span className="ml-1.5 break-all font-mono text-xs text-[var(--color-text-tertiary)]">
          {places[0]!.place}
        </span>
        <StepCheck verify={places[0]!.verify} />
      </>
    );
  }
  if (places.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5 pl-5">
      {places.map((p) => (
        <li key={p.place} className="break-all font-mono text-xs text-[var(--color-text-tertiary)]">
          {p.place}
          <StepCheck verify={p.verify} />
        </li>
      ))}
    </ul>
  );
}

function PlanSteps({ steps }: { steps: PerformanceOpportunityPlanStep[] }) {
  const note = "ml-1.5 text-xs text-[var(--color-text-tertiary)]";
  return (
    <ol className="mt-3 space-y-1.5">
      {groupPlanSteps(steps).map((group, index) => {
        const first = group[0]!;
        return (
          <li key={first.order} className="text-sm text-[var(--color-text-secondary)]">
            <span className="tabular-nums text-[var(--color-text-tertiary)]">{index + 1}.</span>{" "}
            {first.action}
            {group.length > 1 ? <span className={note}>{`at ${group.length} call sites`}</span> : null}
            {first.applicability === "mechanical" ? <span className={note}>mechanical</span> : null}
            <StepPlaces places={placesOf(group)} />
          </li>
        );
      })}
    </ol>
  );
}

/**
 * How to check one step, when it differs from the plan: the server omits
 * `verify` on a step the plan's validation already checks.
 */
function StepCheck({ verify }: { verify: StepVerify | undefined }) {
  if (!verify) return null;
  if (verify.coverage === "none") {
    return (
      <p className="mt-1 font-sans text-xs text-[var(--color-text-tertiary)]">
        No test reaches this step.
      </p>
    );
  }
  return (
    <>
      {verify.commands.map((command) => (
        <CommandLine key={command} command={command} />
      ))}
    </>
  );
}

/** The proposed fix in a sentence, with its concrete construct when named. */
function ProposedFix({ fix }: { fix: NonNullable<PerformanceOpportunity["fix"]> }) {
  return (
    <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
      Proposed intervention: {fixStrategyLabel(fix.strategy).toLowerCase()}
      {fix.api ? (
        <>
          {" "}
          (<code className="font-mono text-xs">{fix.api}</code>)
        </>
      ) : null}
      . {fix.rationale}
    </p>
  );
}

/** Where the stored plan stands: unreachable, loading, failed or stale. */
function StoredPlanStatus({
  plan,
  enabled,
}: {
  plan: ReturnType<typeof useVerifiedPlan>;
  enabled: boolean;
}) {
  const { planId, data, error, isLoading, matches } = plan;
  const quiet = "mt-2 text-xs text-[var(--color-text-tertiary)]";
  if (planId && !enabled) {
    return (
      <p className={quiet}>
        This host cannot open the stored plan. Its id is{" "}
        <span className="break-all font-mono">{planId}</span>.
      </p>
    );
  }
  if (planId && isLoading) return <p className={quiet}>Checking the stored plan.</p>;
  if (planId && error) {
    return <p className={quiet}>The stored plan could not be loaded, so it is not offered here.</p>;
  }
  if (data && !matches) {
    return (
      <p className="mt-2 text-sm text-[var(--color-warning)]">
        The stored plan no longer names this opportunity. Update the index before handing it to an
        agent.
      </p>
    );
  }
  return null;
}

export function PlanSection({
  opportunity,
  adapter,
  enabled,
  plan,
}: {
  opportunity: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
  enabled: boolean;
  plan: ReturnType<typeof useVerifiedPlan>;
}) {
  const presentation = planPresentation(opportunity);
  const href =
    plan.planId && plan.matches
      ? adapter.refactoringPlanHref?.(plan.planId, opportunity.opportunity_id)
      : undefined;
  const steps = opportunity.plan_steps ?? [];

  return (
    <Section title="Plan">
      <p className="text-sm font-medium text-[var(--color-text-primary)]">{presentation.label}</p>
      <OpportunityTags opportunity={opportunity} />
      <p className="mt-1 text-sm text-[var(--color-text-tertiary)]">{presentation.detail}</p>
      {opportunity.fix ? <ProposedFix fix={opportunity.fix} /> : null}
      {steps.length > 0 ? <PlanSteps steps={steps} /> : null}
      <StoredPlanStatus plan={plan} enabled={enabled} />
      {href ? (
        <a
          href={href}
          className="mt-2 inline-block rounded text-sm font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          Open the plan on Refactoring
        </a>
      ) : null}
    </Section>
  );
}
