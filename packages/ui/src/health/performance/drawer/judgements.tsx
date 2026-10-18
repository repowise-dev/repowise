"use client";

import type {
  PerformanceOpportunity,
  PerformanceOpportunityDetail,
  PerformanceOpportunityValidation,
} from "@repowise-dev/types/health";
import type { RecommendationValidation } from "@repowise-dev/types/refactoring";

import { formatNumber } from "../../../lib/format";
import { CollapsibleSection } from "../../../shared/collapsible-section";
import { ProvenancePathList } from "../../../shared/provenance-path-list";
import { ValidationSummary } from "../../../refactoring/validation-summary";
import type { PerformanceViewAdapter } from "../adapter";
import {
  ACTIONABILITY_LABEL,
  affectedSummary,
  agentHandoffCall,
  boundaryLabel,
  CONFIDENCE_LABEL,
  contextLabel,
  FACET_GLOSSARY,
  humanizeToken,
  whyRankedLabel,
} from "../presentation";
import { Field, Section } from "./layout";

/** The judgement and evidence sections, most of them collapsed. The three
 *  judgements stay separate: evidence confidence, actionability, fix safety. */

/** The queue's small validation shape, adapted onto the refactoring surface's
 *  reader rather than duplicated. The fields it doesn't materialize (per-target
 *  breakdown, affected paths) read as empty, not missing. */
function toRecommendationValidation(
  validation: PerformanceOpportunityValidation,
): RecommendationValidation {
  return {
    basis: validation.basis,
    via: validation.via,
    total: validation.total,
    tests: validation.tests,
    truncated: false,
    affected_files: [],
    affected_symbols: [],
    commands: validation.commands,
    targets: [],
    ...(validation.reasons ? { reasons: validation.reasons } : {}),
  };
}

export function ChangeabilitySection({ current }: { current: PerformanceOpportunity }) {
  return (
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
  );
}

export function TestsSection({
  current,
  adapter,
}: {
  current: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
}) {
  if (!current.validation) return null;
  return (
    <CollapsibleSection
      title="Tests"
      hint={
        current.validation.total > 0
          ? `${formatNumber(current.validation.total)} guarding`
          : "none guarding"
      }
      defaultOpen={current.validation.total === 0}
    >
      <ValidationSummary
        validation={toRecommendationValidation(current.validation)}
        fileHref={adapter.fileHref}
      />
    </CollapsibleSection>
  );
}

export function EvidenceSection({ current }: { current: PerformanceOpportunity }) {
  return (
    <CollapsibleSection title="What the evidence shows">
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
          hint={FACET_GLOSSARY.amplification}
        />
        <Field
          label="Exposure"
          value={humanizeToken(current.facets.exposure)}
          hint={FACET_GLOSSARY.exposure}
        />
        <Field
          label="Leverage"
          value={humanizeToken(current.facets.leverage)}
          hint={FACET_GLOSSARY.leverage}
        />
        <Field
          label="Change risk"
          value={humanizeToken(current.facets.change_risk)}
          hint={FACET_GLOSSARY.change_risk}
        />
        {current.facets.loop_magnitude && current.facets.loop_magnitude !== "n/a" ? (
          <Field
            label="Loop magnitude"
            value={humanizeToken(current.facets.loop_magnitude)}
            hint={FACET_GLOSSARY.loop_magnitude}
          />
        ) : null}
      </dl>
    </CollapsibleSection>
  );
}

export function WhyRankedSection({ current }: { current: PerformanceOpportunity }) {
  return (
    <CollapsibleSection title="Why it ranks here">
      <p className="text-sm text-[var(--color-text-secondary)]">
        Position <span className="tabular-nums">{(current.rank_position + 1).toLocaleString()}</span>{" "}
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
    </CollapsibleSection>
  );
}

export function CallerPathsSection({
  current,
  adapter,
}: {
  current: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
}) {
  if (!current.evidence.some((item) => item.path.length > 0)) return null;
  return (
    <CollapsibleSection title="Caller to sink paths">
      <ProvenancePathList
        paths={current.evidence
          .filter((item) => item.path.length > 0)
          .map((item) => ({ nodes: item.path, provenance: item.provenance }))}
        total={current.observations_total}
        fileHref={adapter.fileHref}
        {...(adapter.symbolHref ? { symbolHref: adapter.symbolHref } : {})}
      />
    </CollapsibleSection>
  );
}

export function AgentHandoffSection({
  current,
  resolved,
}: {
  current: PerformanceOpportunity;
  resolved: Extract<PerformanceOpportunityDetail, { found: true }> | null;
}) {
  return (
    <CollapsibleSection title="Hand this to an agent">
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
    </CollapsibleSection>
  );
}
