"use client";

import { useState } from "react";
import useSWR from "swr";
import { Gauge, ChevronRight, Sparkles } from "lucide-react";
import type {
  HealthFinding,
  PerformanceOpportunity,
  PerformanceOpportunityEvidence,
  PerformanceOpportunityPage,
} from "@repowise-dev/types/health";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { EmptyState } from "../shared/empty-state";
import { PageLede } from "../shared/page-lede";
import { PaginationControls } from "../shared/pagination-controls";
import { ProvenancePathList } from "../shared/provenance-path-list";
import { ViewTabs } from "../shared/view-tabs";
import { Sheet, SheetContent, SheetTitle } from "../ui/sheet";
import { StatRibbon } from "../stats/stat-ribbon";
import { RefactoringDrawer } from "../refactoring/refactoring-drawer";
import { performancePlanDetail } from "../refactoring/types";
import { PERF_BOUNDARY_LABEL } from "@repowise-dev/types/health";
import { biomarkerLabel } from "./biomarker-glossary";
import { BiomarkerList } from "./biomarker-list";
import type { CodeHealthAdapter } from "./code-health-adapter";
import { AiPromptModal } from "./ai-prompt-modal";
import {
  buildPerformanceOpportunityPrompt,
  buildRefactoringPlanPrompt,
} from "./ai-prompt-builder";

export type PerformanceViewAdapter = Pick<
  CodeHealthAdapter,
  | "cacheKey"
  | "listFindings"
  | "getPerformanceOpportunities"
  | "getPerformanceOpportunityFindings"
  | "getRefactoringPlan"
  | "refactoringPlanHref"
  | "fileHref"
  | "symbolHref"
  | "navigate"
>;

const PAGE_SIZE = 20;
const RAW_PAGE_SIZE = 50;
type ContextView = "production_tooling" | "test";

function contextLabel(context: PerformanceOpportunity["execution_context"]): string {
  return context === "test" ? "Test suite" : context === "tooling" ? "Tooling" : "Production";
}

function opportunityTitle(opportunity: PerformanceOpportunity): string {
  const boundary = opportunity.boundary_kind
    ? PERF_BOUNDARY_LABEL[opportunity.boundary_kind].toLowerCase()
    : "repeated";
  if (opportunity.terminal_sink) {
    return `${biomarkerLabel(opportunity.biomarker_type)} reaches ${opportunity.terminal_sink}`;
  }
  return `${biomarkerLabel(opportunity.biomarker_type)} in ${boundary} work`;
}

export function PerformanceView({ adapter }: { adapter: PerformanceViewAdapter }) {
  const [context, setContext] = useState<ContextView>("production_tooling");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<PerformanceOpportunity | null>(null);
  const [promptPlan, setPromptPlan] = useState<RefactoringPlan | null>(null);
  const [promptOpportunity, setPromptOpportunity] = useState<PerformanceOpportunity | null>(null);

  const load = adapter.getPerformanceOpportunities;
  const { data, error, isLoading } = useSWR<PerformanceOpportunityPage>(
    load ? `performance-opportunities:${adapter.cacheKey}:${context}:${offset}` : null,
    () => load!({ context, offset, limit: PAGE_SIZE }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );
  const selectedPlanId = selected?.plan_id ?? null;
  const {
    data: selectedPlan,
    error: selectedPlanError,
    isLoading: selectedPlanLoading,
  } = useSWR<RefactoringPlan>(
    selectedPlanId && adapter.getRefactoringPlan
      ? `performance-plan:${adapter.cacheKey}:${selectedPlanId}`
      : null,
    () => adapter.getRefactoringPlan!(selectedPlanId!),
    { revalidateOnFocus: false },
  );
  const selectedPlanMatches = Boolean(
    selectedPlan &&
      selected &&
      selectedPlan.id === selected.plan_id &&
      selectedPlan.refactoring_type === "performance_fix" &&
      performancePlanDetail(selectedPlan).opportunityId === selected.opportunity_id,
  );

  if (!load) {
    return <LegacyPerformanceFindings adapter={adapter} />;
  }
  if (error && [404, 405].includes(Number((error as { status?: number }).status))) {
    return <LegacyPerformanceFindings adapter={adapter} />;
  }
  if (error) {
    return (
      <EmptyState
        icon={<Gauge className="h-6 w-6" />}
        title="Couldn’t load performance opportunities"
        description="The repository may need indexing, or the server may be temporarily unavailable."
      />
    );
  }
  if (isLoading && !data) {
    return (
      <div className="space-y-8">
        <div className="h-32 animate-pulse bg-[var(--color-bg-surface)]" />
        <div className="h-12 animate-pulse bg-[var(--color-bg-surface)]" />
        <div className="h-72 animate-pulse bg-[var(--color-bg-surface)]" />
      </div>
    );
  }
  if (!data) return null;

  const { summary } = data;
  const productionToolingTotal = summary.production_total + summary.tooling_total;
  const stats = [
    {
      label: "Production",
      value: summary.production_total.toLocaleString(),
      sub: "runtime paths",
    },
    {
      label: "Tooling",
      value: summary.tooling_total.toLocaleString(),
      sub: "build and developer paths",
    },
    {
      label: "Test suite",
      value: summary.test_total.toLocaleString(),
      sub: "test execution cost",
    },
    {
      label: "Structured plan",
      value: summary.with_plan_total.toLocaleString(),
      sub: `of ${summary.total.toLocaleString()} causes`,
    },
  ];

  return (
    <div className="space-y-8">
      <div className="space-y-6">
        <PageLede
          label="Causal opportunities"
          value={summary.total.toLocaleString()}
          unit="ranked root causes, not duplicated observations"
          layout="beside"
          figureFooter={
            <p className="text-caption text-[var(--color-text-tertiary)]">
              Static, high-precision signals. They are not measured latency.
            </p>
          }
        >
          <p>
            Repeated caller paths are folded into one cause so the first row answers what should
            change once, while every raw line finding stays available as evidence.
          </p>
          <p>
            Production and tooling stay separate from test-suite cost. A structured plan appears
            only when the analysis can name an intervention without guessing.
          </p>
        </PageLede>
        <StatRibbon stats={stats} />
      </div>

      <section className="space-y-4 border-t border-[var(--color-border-default)] pt-8">
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">
            Opportunity queue
          </h2>
          <p className="mt-1 max-w-[72ch] text-sm text-[var(--color-text-secondary)]">
            Ordered by the canonical causal score: multiplier shape, boundary, execution context,
            entry reachability, affected call sites, and resolution provenance.
          </p>
        </div>
        <ViewTabs
          tabs={[
            {
              id: "production_tooling",
              label: "Production & tooling",
              badge: productionToolingTotal,
            },
            { id: "test", label: "Test suite", badge: summary.test_total },
          ]}
          value={context}
          onValueChange={(value) => {
            setOffset(0);
            setContext(value as ContextView);
          }}
        />

        {data.items.length === 0 ? (
          <div className="border-t border-[var(--color-border-default)] py-10 text-center">
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
              No causal opportunities in this context
            </h3>
            <p className="mx-auto mt-1.5 max-w-[60ch] text-sm text-[var(--color-text-tertiary)]">
              A supported performance detector must find a repeated cost shape before this queue
              fills. This does not claim the code is fast.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
            {data.items.map((opportunity) => (
              <OpportunityRow
                key={opportunity.opportunity_id}
                opportunity={opportunity}
                onOpen={() => setSelected(opportunity)}
              />
            ))}
          </div>
        )}

        <PaginationControls
          offset={offset}
          shown={data.items.length}
          total={data.total}
          label="opportunities"
          onPrevious={offset > 0 ? () => setOffset(Math.max(0, offset - PAGE_SIZE)) : undefined}
          onNext={data.next_offset != null ? () => setOffset(data.next_offset!) : undefined}
        />
      </section>

      {selectedPlanId && adapter.getRefactoringPlan ? (
        <RefactoringDrawer
          plan={selectedPlanMatches ? (selectedPlan ?? null) : null}
          loading={selectedPlanLoading}
          error={
            selectedPlanError
              ? "The exact plan could not be loaded. Close this drawer and retry from the queue."
              : selectedPlan && !selectedPlanMatches
                ? "The returned plan no longer matches this opportunity. Reindex before handing it to an agent."
              : undefined
          }
          open={selected !== null}
          onOpenChange={(open) => {
            if (!open) setSelected(null);
          }}
          onAiPrompt={(plan) => {
            if (selectedPlanMatches) setPromptPlan(plan);
          }}
          contextSlot={
            selected ? <OpportunityCausalEvidence opportunity={selected} adapter={adapter} /> : null
          }
          fileHref={(path, line) => {
            const href = adapter.fileHref(path);
            return line ? `${href}${href.includes("?") ? "&" : "?"}line=${line}` : href;
          }}
        />
      ) : (
        <PerformanceOpportunityDrawer
          opportunity={selected}
          adapter={adapter}
          onClose={() => setSelected(null)}
          onAiPrompt={setPromptOpportunity}
        />
      )}

      <AiPromptModal
        open={promptPlan !== null || promptOpportunity !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPromptPlan(null);
            setPromptOpportunity(null);
          }
        }}
        getPrompt={
          promptPlan
            ? (flavor) => buildRefactoringPlanPrompt({ plan: promptPlan, flavor })
            : promptOpportunity
              ? (flavor) =>
                  buildPerformanceOpportunityPrompt({ opportunity: promptOpportunity, flavor })
              : null
        }
        filePath={promptPlan?.file_path ?? promptOpportunity?.evidence[0]?.file_path ?? null}
        title={promptPlan ? "AI performance plan" : "AI performance investigation"}
        description={
          promptPlan
            ? "A ready-to-paste structured plan with the intervention, affected targets, validation, and completion contract."
            : "A ready-to-paste evidence handoff that asks an agent to verify the cause before proposing any edit."
        }
      />
    </div>
  );
}

function LegacyPerformanceFindings({ adapter }: { adapter: PerformanceViewAdapter }) {
  const { data, error, isLoading } = useSWR<HealthFinding[]>(
    `performance-findings-legacy:${adapter.cacheKey}`,
    () => adapter.listFindings({ dimension: "performance", limit: 100 }),
    { revalidateOnFocus: false },
  );
  if (isLoading) return <div className="h-72 animate-pulse bg-[var(--color-bg-surface)]" />;
  if (error || !data?.length) {
    return (
      <EmptyState
        icon={<Gauge className="h-6 w-6" />}
        title="No performance opportunities"
        description="This older server does not provide causal grouping. Reindex with a current server to distinguish an empty result from unsupported grouping."
      />
    );
  }
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">
          Raw performance findings
        </h2>
        <p className="mt-1 max-w-[72ch] text-sm text-[var(--color-text-secondary)]">
          This server predates causal opportunity groups. Up to 100 canonical findings are shown
          without inventing plan links or rank semantics.
        </p>
      </div>
      <BiomarkerList
        findings={data}
        grouped
        onSelect={(finding) => adapter.navigate(adapter.fileHref(finding.file_path))}
      />
    </div>
  );
}

function OpportunityRow({
  opportunity,
  onOpen,
}: {
  opportunity: PerformanceOpportunity;
  onOpen: () => void;
}) {
  const boundary = opportunity.boundary_kind
    ? PERF_BOUNDARY_LABEL[opportunity.boundary_kind]
    : "Local computation";

  return (
    <article data-performance-opportunity={opportunity.opportunity_id}>
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Inspect ${opportunity.affected_call_sites_total} call sites for ${opportunityTitle(opportunity)}`}
        className="grid w-full grid-cols-[18px_minmax(0,1fr)] gap-3 px-2 py-4 text-left hover:bg-[var(--color-bg-elevated)] sm:grid-cols-[18px_minmax(0,1fr)_auto] sm:items-center"
      >
        <ChevronRight className="mt-1 h-4 w-4" />
        <span className="min-w-0">
          <span className="block break-words text-[15px] font-semibold text-[var(--color-text-primary)]">
            {opportunityTitle(opportunity)}
          </span>
          <span className="mt-1 block text-xs text-[var(--color-text-secondary)]">
            {boundary} · {contextLabel(opportunity.execution_context)} ·{" "}
            {opportunity.confidence[0]!.toUpperCase() + opportunity.confidence.slice(1)} confidence
          </span>
        </span>
        <span className="col-start-2 text-xs tabular-nums text-[var(--color-text-tertiary)] sm:col-auto sm:text-right">
          {opportunity.affected_call_sites_total.toLocaleString()} call site
          {opportunity.affected_call_sites_total === 1 ? "" : "s"}
          <br />
          {opportunity.affected_files_total.toLocaleString()} file
          {opportunity.affected_files_total === 1 ? "" : "s"}
          <span
            className={`mt-1 block font-medium ${
              opportunity.plan_id
                ? "text-[var(--color-success)]"
                : "text-[var(--color-text-tertiary)]"
            }`}
          >
            {opportunity.plan_id
              ? "Structured plan ready"
              : opportunity.plan_status === "not_persisted"
                ? "Index refresh needed"
                : "Investigation needed"}
          </span>
        </span>
      </button>

    </article>
  );
}

function OpportunityCausalEvidence({
  opportunity,
  adapter,
}: {
  opportunity: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
}) {
  const paths = opportunity.evidence
    .filter((item) => item.path.length > 0)
    .map((item) => ({ nodes: item.path, provenance: item.provenance }));
  return (
    <div className="space-y-6">
      <section>
        <h4 className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          Causal context
        </h4>
        <p className="text-sm leading-relaxed text-[var(--color-text-secondary)]">
          {contextLabel(opportunity.execution_context)} · {opportunity.confidence} confidence ·{" "}
          {opportunity.provenance.replaceAll("-", " ")} provenance.{" "}
          {opportunity.intervention_symbol ? (
            <>
              Repeated work converges at{" "}
              <span className="break-all font-mono text-xs text-[var(--color-text-primary)]">
                {opportunity.intervention_symbol}
              </span>
              .
            </>
          ) : (
            <>The paths share a costly sink, but no safe common intervention was proven.</>
          )}
        </p>
      </section>
      <section>
        <h4 className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          Caller-to-sink paths
        </h4>
        <ProvenancePathList
          paths={paths}
          total={opportunity.observations_total}
          fileHref={adapter.fileHref}
          symbolHref={adapter.symbolHref}
        />
      </section>
      <RawEvidence opportunity={opportunity} adapter={adapter} />
    </div>
  );
}

function PerformanceOpportunityDrawer({
  opportunity,
  adapter,
  onClose,
  onAiPrompt,
}: {
  opportunity: PerformanceOpportunity | null;
  adapter: PerformanceViewAdapter;
  onClose: () => void;
  onAiPrompt: (opportunity: PerformanceOpportunity) => void;
}) {
  const planHref = opportunity?.plan_id
    ? adapter.refactoringPlanHref?.(opportunity.plan_id, opportunity.opportunity_id)
    : undefined;

  return (
    <Sheet open={opportunity !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side="right"
        closeLabel="Close opportunity"
        className="w-full max-w-[680px] sm:w-[92vw]"
      >
        {opportunity ? (
          <>
            <div className="border-b border-[var(--color-border-default)] px-5 py-4 pr-12">
              <div className="text-[11px] text-[var(--color-text-secondary)]">
                Causal performance opportunity
              </div>
              <SheetTitle className="mt-0.5 break-words text-[15px] font-semibold text-[var(--color-text-primary)]">
                {opportunityTitle(opportunity)}
              </SheetTitle>
              <p className="mt-1 text-[11.5px] text-[var(--color-text-tertiary)]">
                {contextLabel(opportunity.execution_context)} · {opportunity.confidence} confidence ·{" "}
                {opportunity.affected_call_sites_total} call sites
              </p>
            </div>

            <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
              <OpportunityCausalEvidence opportunity={opportunity} adapter={adapter} />

              <section>
                <h4 className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                  Recommended next step
                </h4>
                <p className="text-sm font-medium text-[var(--color-text-primary)]">
                  {opportunity.plan_status === "not_persisted"
                    ? "Refresh the index to materialize the plan"
                    : opportunity.plan_id
                      ? "Review the matching structured plan"
                      : "No safe structured plan — investigate before changing code"}
                </p>
                <p className="mt-1 text-sm text-[var(--color-text-tertiary)]">
                  {opportunity.plan_reason}
                </p>
                {planHref ? (
                  <a
                    href={planHref}
                    className="mt-2 inline-block text-sm font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline"
                  >
                    Open on Refactoring page
                  </a>
                ) : null}
              </section>
            </div>

            <div className="flex items-center gap-3 border-t border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-5 py-3.5">
              <button
                type="button"
                onClick={() => onAiPrompt(opportunity)}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-[var(--color-accent-fill)] px-3.5 py-2 text-sm font-semibold text-[var(--color-text-on-accent)] transition-opacity hover:opacity-90"
              >
                <Sparkles className="h-4 w-4" />
                Copy investigation for an agent
              </button>
            </div>
          </>
        ) : (
          <SheetTitle className="sr-only">Performance opportunity</SheetTitle>
        )}
      </SheetContent>
    </Sheet>
  );
}

function RawEvidence({
  opportunity,
  adapter,
}: {
  opportunity: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
}) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const load = adapter.getPerformanceOpportunityFindings;
  const { data, isLoading } = useSWR(
    open && load
      ? `performance-raw:${adapter.cacheKey}:${opportunity.opportunity_id}:${offset}`
      : null,
    () => load!(opportunity.opportunity_id, { offset, limit: RAW_PAGE_SIZE }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );
  const fallback = opportunity.evidence;
  const items: Array<HealthFinding | PerformanceOpportunityEvidence> = data?.items ?? fallback;
  const total = data?.total ?? opportunity.observations_total;

  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="text-sm font-medium text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-text-primary)] hover:underline"
      >
        {open ? "Hide" : "Review"} raw findings · {total.toLocaleString()} observation
        {total === 1 ? "" : "s"}
      </button>
      {open ? (
        <div className="mt-3">
          {isLoading && !data ? (
            <div className="h-24 animate-pulse bg-[var(--color-bg-surface)]" />
          ) : (
            <div className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
              {items.map((finding, index) => {
                const href = adapter.fileHref(finding.file_path);
                return (
                  <div
                    key={("id" in finding ? finding.id : finding.finding_id) || index}
                    className="py-3"
                  >
                    <a
                      href={href}
                      className="break-all font-mono text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-accent-primary)] hover:underline"
                    >
                      {finding.file_path}
                      {finding.line_start ? `:${finding.line_start}` : ""}
                    </a>
                    <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
                      {finding.reason}
                    </p>
                  </div>
                );
              })}
            </div>
          )}
          {data ? (
            <PaginationControls
              offset={offset}
              shown={data.items.length}
              total={data.total}
              label="raw findings"
              onPrevious={
                offset > 0 ? () => setOffset(Math.max(0, offset - RAW_PAGE_SIZE)) : undefined
              }
              onNext={data.next_offset != null ? () => setOffset(data.next_offset!) : undefined}
            />
          ) : opportunity.evidence_truncated ? (
            <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
              {fallback.length.toLocaleString()} shown of {total.toLocaleString()}; connect the
              paged evidence endpoint to load the rest.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
