"use client";

import useSWR from "swr";
import { Sparkles } from "lucide-react";
import type {
  PerformanceOpportunity,
  PerformanceOpportunityDetail,
  PerformanceModelState,
} from "@repowise-dev/types/health";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { AdaptivePanel } from "../../shared/adaptive-panel";
import { ProvenancePathList } from "../../shared/provenance-path-list";
import { performancePlanDetail } from "../../refactoring/types";
import type { PerformanceViewAdapter } from "./adapter";
import { RawObservations } from "./evidence";
import {
  ACTIONABILITY_LABEL,
  affectedSummary,
  agentHandoffCall,
  boundaryLabel,
  CONFIDENCE_LABEL,
  contextLabel,
  humanizeToken,
  opportunityEvidenceLine,
  opportunityTitle,
  planPresentation,
  whyRankedLabel,
} from "./presentation";

/**
 * One opportunity in depth.
 *
 * The three judgements the analysis makes separately are shown separately:
 * how well the evidence resolved, whether reducing the work is likely valid,
 * and whether one named transformation is safe. Collapsing them would let
 * strong evidence read as a safe fix.
 */

function Field({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string | undefined;
}) {
  return (
    <div className="min-w-0">
      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm font-medium text-[var(--color-text-primary)]">{value}</dd>
      {detail ? (
        <dd className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">{detail}</dd>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {title}
      </h4>
      {children}
    </section>
  );
}

/** A resolved id that came from another model is not the same as a wrong id. */
function ModelStateNotice({ state }: { state: PerformanceModelState }) {
  if (state.state === "current") return null;
  const message =
    state.state === "stale_model"
      ? `This id was minted by performance model version ${state.requested_model_version ?? "an earlier release"}; the index now serves version ${state.performance_model_version}.`
      : "This index does not recognize that opportunity id.";
  return (
    <p
      role="status"
      className="border-l-2 border-[var(--color-warning)] py-1.5 pl-3 text-sm text-[var(--color-text-secondary)]"
    >
      {message}
      {state.refresh_required ? " Update the index to get the current grouping." : ""}
    </p>
  );
}

/**
 * Fetch the stored plan and decide whether it names this exact opportunity.
 * A reindexed or reused row must never be handed over as this one's fix, so
 * both the link and the agent handoff wait on the same verdict.
 */
function useVerifiedPlan(
  opportunity: PerformanceOpportunity | null,
  adapter: PerformanceViewAdapter,
  enabled: boolean,
) {
  const planId =
    opportunity && planPresentation(opportunity).actionable ? opportunity.plan_id : null;
  const { data, error, isLoading } = useSWR<RefactoringPlan>(
    planId && enabled ? `performance-plan:${adapter.cacheKey}:${planId}` : null,
    () => adapter.getRefactoringPlan!(planId!),
    { revalidateOnFocus: false },
  );
  const matches = Boolean(
    data &&
      opportunity &&
      data.id === opportunity.plan_id &&
      data.refactoring_type === "performance_fix" &&
      performancePlanDetail(data).opportunityId === opportunity.opportunity_id,
  );
  return { planId, data, error, isLoading, matches, verified: matches ? data! : null };
}

function PlanSection({
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
  const { planId, data, error, isLoading, matches } = plan;
  const href =
    planId && matches
      ? adapter.refactoringPlanHref?.(planId, opportunity.opportunity_id)
      : undefined;

  return (
    <Section title="Plan">
      <p className="text-sm font-medium text-[var(--color-text-primary)]">{presentation.label}</p>
      <p className="mt-1 text-sm text-[var(--color-text-tertiary)]">{presentation.detail}</p>
      {opportunity.fix ? (
        <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
          Proposed intervention: {humanizeToken(opportunity.fix.strategy).toLowerCase()}.{" "}
          {opportunity.fix.rationale}
        </p>
      ) : null}
      {planId && !enabled ? (
        <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
          This host cannot open the stored plan. Its id is{" "}
          <span className="break-all font-mono">{planId}</span>.
        </p>
      ) : null}
      {planId && enabled && isLoading ? (
        <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">Checking the stored plan.</p>
      ) : null}
      {planId && enabled && error ? (
        <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
          The stored plan could not be loaded, so it is not offered here.
        </p>
      ) : null}
      {data && !matches ? (
        <p className="mt-2 text-sm text-[var(--color-warning)]">
          The stored plan no longer names this opportunity. Update the index before handing it to
          an agent.
        </p>
      ) : null}
      {href ? (
        <a
          href={href}
          className="mt-2 inline-block rounded text-sm font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          Open the plan on Refactoring
        </a>
      ) : null}
    </Section>
  );
}

export function OpportunityDrawer({
  opportunity,
  adapter,
  detailEnabled,
  planEnabled,
  onClose,
  onAgentHandoff,
}: {
  /** The row that was inspected, held so the panel can render before the fetch. */
  opportunity: PerformanceOpportunity | null;
  adapter: PerformanceViewAdapter;
  detailEnabled: boolean;
  planEnabled: boolean;
  onClose: () => void;
  onAgentHandoff: (opportunity: PerformanceOpportunity, plan: RefactoringPlan | null) => void;
}) {
  const id = opportunity?.opportunity_id ?? null;
  const { data: detail } = useSWR<PerformanceOpportunityDetail>(
    id && detailEnabled ? `performance-opportunity:${adapter.cacheKey}:${id}` : null,
    () => adapter.getPerformanceOpportunity!(id!, { evidenceLimit: 8 }),
    { revalidateOnFocus: false },
  );

  // The row is the fallback body: a host without the detail call still reads
  // every field the queue carried, and simply learns nothing extra.
  const resolved = detail && detail.resolved ? detail : null;
  const unresolved = detail && !detail.resolved ? detail : null;
  const current: PerformanceOpportunity | null = resolved ?? opportunity;
  const plan = useVerifiedPlan(current, adapter, planEnabled);

  return (
    <AdaptivePanel
      open={opportunity !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      eyebrow="Performance opportunity"
      title={current ? opportunityTitle(current) : "Performance opportunity"}
      widthClassName="md:max-w-[680px]"
    >
      {current ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex-1 space-y-7 overflow-y-auto px-5 py-5">
            <p className="break-all font-mono text-xs text-[var(--color-text-secondary)]">
              {opportunityEvidenceLine(current)}
            </p>

            {unresolved ? <ModelStateNotice state={unresolved.model_state} /> : null}
            {resolved ? <ModelStateNotice state={resolved.model_state} /> : null}
            {unresolved ? (
              <p className="text-sm text-[var(--color-text-tertiary)]">{unresolved.detail}</p>
            ) : null}
            {resolved && resolved.lifecycle_status === "resolved" ? (
              <p
                role="status"
                className="border-l-2 border-[var(--color-warning)] py-1.5 pl-3 text-sm text-[var(--color-text-secondary)]"
              >
                No observation supports this cause in the current index. It is kept so a saved
                link still resolves.
              </p>
            ) : null}

            <MapLink adapter={adapter} opportunity={current} />

            <Section title="What the evidence shows">
              <dl className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
                <Field label="Context" value={contextLabel(current.execution_context)} />
                <Field label="Boundary" value={boundaryLabel(current.boundary_kind)} />
                <Field
                  label="Evidence confidence"
                  value={CONFIDENCE_LABEL[current.confidence]}
                  detail={`How reliably the call path resolved. Provenance: ${humanizeToken(current.provenance).toLowerCase()}.`}
                />
                <Field
                  label="Affected"
                  value={affectedSummary(current)}
                  detail={`${current.observations_total.toLocaleString()} observation${current.observations_total === 1 ? "" : "s"} fold into this cause.`}
                />
                <Field
                  label="Amplification"
                  value={humanizeToken(current.facets.amplification)}
                />
                <Field label="Exposure" value={humanizeToken(current.facets.exposure)} />
                <Field label="Leverage" value={humanizeToken(current.facets.leverage)} />
                <Field label="Change risk" value={humanizeToken(current.facets.change_risk)} />
              </dl>
            </Section>

            <Section title="Whether it can be changed">
              <dl className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
                <Field
                  label="Actionability"
                  value={ACTIONABILITY_LABEL[current.actionability_state]}
                  detail={humanizeToken(current.actionability_reason)}
                />
                <Field
                  label="Actionability confidence"
                  value={CONFIDENCE_LABEL[current.facets.actionability_confidence]}
                  detail="Whether reducing the work is likely valid."
                />
                <Field
                  label="Fix safety"
                  value={current.fix ? humanizeToken(current.fix.safety) : "No named fix"}
                  detail={
                    current.fix
                      ? "Whether this one transformation is safe to apply."
                      : "No transformation was proposed, so none was judged safe."
                  }
                />
              </dl>
              {current.prerequisites.length > 0 ? (
                <div className="mt-3">
                  <p className="text-xs text-[var(--color-text-tertiary)]">
                    Missing before a fix can be named:
                  </p>
                  <ul className="mt-1 list-inside list-disc text-xs text-[var(--color-text-secondary)]">
                    {current.prerequisites.map((item) => (
                      <li key={item}>{humanizeToken(item)}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </Section>

            <Section title="Why it ranks here">
              <p className="text-sm text-[var(--color-text-secondary)]">
                Position <span className="tabular-nums">{current.rank_position.toLocaleString()}</span>{" "}
                in the queue.
              </p>
              {current.why_ranked.length > 0 ? (
                <ul className="mt-2 space-y-1 text-sm text-[var(--color-text-secondary)]">
                  {current.why_ranked.map((factor) => (
                    <li key={factor.factor} className="tabular-nums">
                      {whyRankedLabel(factor)}
                    </li>
                  ))}
                </ul>
              ) : null}
            </Section>

            <PlanSection
              opportunity={current}
              adapter={adapter}
              enabled={planEnabled}
              plan={plan}
            />

            {current.evidence.some((item) => item.path.length > 0) ? (
              <Section title="Caller to sink paths">
                <ProvenancePathList
                  paths={current.evidence
                    .filter((item) => item.path.length > 0)
                    .map((item) => ({ nodes: item.path, provenance: item.provenance }))}
                  total={current.observations_total}
                  fileHref={adapter.fileHref}
                  {...(adapter.symbolHref ? { symbolHref: adapter.symbolHref } : {})}
                />
              </Section>
            ) : null}

            <RawObservations
              opportunityId={current.opportunity_id}
              preview={current.evidence}
              previewTruncated={current.evidence_truncated}
              total={current.observations_total}
              adapter={adapter}
            />

            <Section title="Hand this to an agent">
              <p className="text-sm text-[var(--color-text-secondary)]">
                Ask for the same record by its stable id:
              </p>
              <p className="mt-1 break-all rounded bg-[var(--color-bg-inset)] px-2 py-1.5 font-mono text-xs text-[var(--color-text-primary)]">
                {agentHandoffCall(current.opportunity_id)}
              </p>
              {resolved?.analyzed_commit ? (
                <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                  Analyzed at{" "}
                  <span className="font-mono">{resolved.analyzed_commit.slice(0, 7)}</span>.
                </p>
              ) : null}
            </Section>
          </div>

          <div className="flex items-center gap-3 border-t border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-5 py-3.5">
            <button
              type="button"
              onClick={() => onAgentHandoff(current, plan.verified)}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-[var(--color-model)] px-3.5 py-2 text-sm font-semibold text-[var(--color-text-on-model)] transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
            >
              <Sparkles className="h-4 w-4" aria-hidden="true" />
              {plan.verified ? "Copy the plan for an agent" : "Copy an agent handoff"}
            </button>
          </div>
        </div>
      ) : null}
    </AdaptivePanel>
  );
}

/**
 * Where this cause sits on the one map.
 *
 * A link rather than a second canvas: the galaxy already exists, it already
 * knows how to guarantee a file a node, and drawing a small copy of it here
 * would put two fields with two geometries on one screen.
 */
function MapLink({
  adapter,
  opportunity,
}: {
  adapter: PerformanceViewAdapter;
  opportunity: PerformanceOpportunity;
}) {
  const href = adapter.mapHref?.(opportunity.opportunity_id, opportunity.file_path);
  if (!href || !opportunity.file_path) return null;
  return (
    <a
      href={href}
      className="inline-block rounded text-sm font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
    >
      Show this cause on the map
    </a>
  );
}
