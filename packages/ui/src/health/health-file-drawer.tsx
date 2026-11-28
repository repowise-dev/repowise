"use client";

/**
 * One file's health, beside the map or list it was opened from.
 *
 * Built so a developer who has never seen Repowise knows in five seconds how
 * bad the file is, what is wrong in plain words, and what to do next: the
 * verdict (score, band, trend, one sentence, one action), the top three issues,
 * and a strip of vital signs. Everything else is one click away, collapsed,
 * each disclosure with a hint that does not repeat its body.
 *
 * Colour is health state only. The accent sits on the one primary action;
 * links are ink. It is non-modal, so the surface behind it stays live and
 * picking another file swaps the content in place.
 *
 * This file is the shell and the public types; the sections live in
 * `./file-drawer/`, and `useDrawerModel` derives everything they show.
 */

import { useState } from "react";
import { AdaptivePanel } from "../shared/adaptive-panel";
import { ApiError } from "../shared/api-error";
import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import { toFriendlyMessage } from "../lib/errors";
import { cn } from "../lib/cn";
import type { BiomarkerDimension } from "./biomarker-glossary";
import type { BiomarkerDetailsRecord } from "./biomarker-details";
import type { ScoreBreakdownCategory } from "./score-breakdown";
import type { RelatedWorkSlotProps } from "./related-work";
import type {
  FileHealthTrend,
  FileSignals,
  GatedByLanguage,
  PerformanceOpportunity,
} from "@repowise-dev/types/health";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";
import type { Severity } from "./tokens";
import { useDrawerModel, useTriage } from "./file-drawer/model";
import { FilePromptModal, VerdictSection } from "./file-drawer/verdict";
import { FindingDetail, OtherIssues, WhatsWrong, type RowContext } from "./file-drawer/issues";
import { VitalSigns } from "./file-drawer/vital-signs";
import {
  DependenciesSection,
  DetailsSection,
  ElsewhereSection,
  HistorySection,
  WhyScoreSection,
} from "./file-drawer/sections";
import { PerformanceCauses } from "./file-drawer/performance-causes";

export interface HealthDrawerFinding {
  id: string;
  biomarker_type: string;
  severity: Severity;
  function_name: string | null;
  line_start: number | null;
  line_end: number | null;
  health_impact: number;
  reason: string;
  status?: string;
  details?: BiomarkerDetailsRecord | null;
  /** Home pillar; falls back to the biomarker's glossary dimension. */
  dimension?: BiomarkerDimension | string;
  /** `"unverified"` for a provisional finding type. */
  verification?: string | null;
  lower_priority?: string | null;
  /** The measurement note when the row's language cell is served as a preview;
   *  null or absent for a validated cell. */
  preview?: string | null;
}

export interface HealthDrawerMetric {
  file_path: string;
  /** `null` when health has no dialect for the file's language. */
  score: number | null;
  /** Structural counters: null when the host has no metric row for the
   *  file, so the drawer can say "not measured" instead of a misleading 0. */
  max_ccn: number | null;
  max_nesting: number | null;
  nloc: number | null;
  module: string | null;
  duplication_pct?: number | null;
  line_coverage_pct?: number | null;
  has_test_file: boolean;
  /** Per-dimension scores from the three-signal split (null until populated). */
  defect_score?: number | null;
  maintainability_score?: number | null;
  performance_score?: number | null;
  /** Dominant-cause lead + pre-clamp deduction magnitude (null when absent). */
  primary_biomarker?: string | null;
  primary_reason?: string | null;
  total_deduction?: number | null;
}

export interface HealthFileDrawerProps extends RelatedWorkSlotProps {
  open: boolean;
  /**
   * Render the same content in place, for a host that has a rail for it (the
   * area map), instead of in a sheet. `onClose` is then the host's to offer.
   */
  inline?: boolean;
  onClose: () => void;
  /**
   * The file the reader asked for. Optional: without it the header follows
   * `metric`. With it the header names the new file at once while the body
   * still shows the previous one, dimmed, until its data lands, which is
   * what a host that keeps previous data on a key change wants.
   */
  filePath?: string | null | undefined;
  loading?: boolean;
  /** The breakdown request failed. Shown with a retry instead of "no metric". */
  error?: unknown;
  onRetry?: (() => void) | undefined;
  metric?: HealthDrawerMetric | null;
  breakdown?: {
    score: number;
    total_deduction: number;
    categories: ScoreBreakdownCategory[];
  } | null;
  findings?: HealthDrawerFinding[];
  suggestions?: Record<string, string>;
  /** What a language gate held back from `findings`, so the list can say so. */
  heldBack?: GatedByLanguage | null | undefined;
  /**
   * The file's composed refactoring opportunity, when the host can supply one.
   * It becomes the lede's primary action, and findings whose cause it
   * addresses link to it; the rest do not, so the drawer never offers a plan
   * for a problem the plan does not answer.
   */
  opportunity?: RefactoringOpportunity | null | undefined;
  refactoringOpportunityHref?: ((opportunityId: string) => string) | undefined;
  /** Per-file score trajectory; renders a compact sparkline when populated. */
  trend?: FileHealthTrend | null;
  /** Process / people / topology signals; their sections are silent when absent. */
  signals?: FileSignals | null;
  fileViewHref?: string;
  /**
   * Build a per-line deep-link from the drawer's function:line span. Unbound
   * until a file view renders line anchors: no `#L<n>` resolves today, so a
   * host that supplies one only adds a fragment that goes nowhere. Without
   * it the label falls back to `fileViewHref`, the file page itself.
   */
  fileViewHrefFor?: ((lineStart: number) => string) | undefined;
  permalinkHref?: string;
  onPartnerSelect?: ((path: string) => void) | undefined;
  onPartnerHref?: ((path: string) => string) | undefined;
  /** Triage callback: PATCH the finding status. The row menu hides when absent. */
  onFindingStatusChange?:
    | ((findingId: string, status: string) => Promise<void> | void)
    | undefined;
  /**
   * The surface this file was opened from. In `performance` the drawer leads
   * with the file's causes, because that is what the reader was looking at.
   * Anything else renders the default.
   */
  lens?: string;
  /**
   * The file's open performance causes, when the host fetched them. `null` is
   * "the host did not ask", which the section says rather than drawing an
   * empty list that reads as a clear file.
   */
  performance?: { items: PerformanceOpportunity[]; total: number } | null;
  performanceLoading?: boolean;
  /** Open one cause on the performance surface. */
  onOpportunitySelect?: ((opportunityId: string) => void) | undefined;
  /** Named in the agent prompt so a pasted prompt says which repo it is for. */
  repoName?: string;
}

/** A non-modal drawer stays open while the reader works the surface behind it. */
const keepOpen = (event: Event) => event.preventDefault();

/** Where the requested file stands against the data on hand. */
function drawerView(props: HealthFileDrawerProps) {
  const { filePath, metric, error } = props;
  const shownPath = metric?.file_path ?? null;
  const headerPath = filePath ?? shownPath;
  // Previous data on screen under a new request: name the new file, dim the old.
  const stale =
    filePath != null && shownPath != null && filePath !== shownPath && error == null;
  return { headerPath, stale };
}

export function HealthFileDrawer(props: HealthFileDrawerProps) {
  const { open, onClose, loading, metric } = props;
  const { headerPath, stale } = drawerView(props);
  const body = (
    <div
      className={cn(
        "flex flex-col gap-6 transition-opacity motion-reduce:transition-none",
        props.inline ? "py-2" : "px-4 py-4",
        stale && "pointer-events-none opacity-60",
      )}
      aria-busy={stale || Boolean(loading && !metric)}
      // The previous file's body is a placeholder, not a surface: a triage
      // write from it would revalidate the new file's key and leave the old
      // one's cache stale.
      inert={stale}
    >
      <DrawerContent props={props} headerPath={headerPath} />
    </div>
  );
  if (props.inline) {
    if (!open) return null;
    return (
      <section data-testid="file-detail-inline" className="flex min-w-0 flex-col">
        {headerPath ? (
          <h3 className="flex min-w-0 flex-col gap-0.5">
            <FileTitle path={headerPath} />
          </h3>
        ) : null}
        {body}
      </section>
    );
  }
  return (
    <AdaptivePanel
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      // Non-modal: the map or list behind stays interactive, so clicking the
      // next file swaps this content instead of fighting a focus trap.
      // Escape and the close button still dismiss it.
      modal={false}
      onInteractOutside={keepOpen}
      title={headerPath ? <FileTitle path={headerPath} /> : "Loading…"}
      titleClassName="mt-0.5 flex min-w-0 flex-col gap-0.5"
      widthClassName="md:max-w-[560px]"
    >
      {body}
    </AdaptivePanel>
  );
}

/** Error, loading, nothing yet, or the file. */
function DrawerContent({
  props,
  headerPath,
}: {
  props: HealthFileDrawerProps;
  headerPath: string | null;
}) {
  const { error, onRetry, loading, metric } = props;
  if (error != null && (!metric || metric.file_path !== headerPath)) {
    return (
      <ApiError
        size="compact"
        title="Couldn't load this file"
        message={toFriendlyMessage(error)}
        {...(onRetry ? { onRetry } : {})}
      />
    );
  }
  if (!metric) {
    return loading ? (
      <DrawerSkeleton />
    ) : (
      <p className="text-xs text-[var(--color-text-secondary)]">
        No metric for this file yet. It appears after the next index or sync.
      </p>
    );
  }
  // Keyed by file, so triage overrides, an open prompt and every disclosure
  // start fresh on the next file rather than carrying over.
  return <DrawerBody key={metric.file_path} {...props} metric={metric} />;
}

/** The file name leads; the directory is the small machine fact under it. */
function FileTitle({ path }: { path: string }) {
  const cut = path.lastIndexOf("/");
  const name = cut >= 0 ? path.slice(cut + 1) : path;
  const dir = cut >= 0 ? path.slice(0, cut + 1) : "";
  return (
    <>
      <span className="block break-words text-[18px] font-semibold leading-snug text-[var(--color-text-primary)]">
        {name}
      </span>
      {dir ? (
        <span className="block break-all font-mono text-xs leading-snug text-[var(--color-text-tertiary)]">
          {dir}
        </span>
      ) : null}
    </>
  );
}

/** Same shapes as the lede and the first rows, so the content lands without a jump. */
function DrawerSkeleton() {
  return (
    <SkeletonRegion className="flex flex-col gap-6" label="Loading file health">
      <div className="flex flex-col gap-4 sm:flex-row sm:gap-6">
        <Skeleton className="h-[72px] w-[150px]" />
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="mt-2 h-7 w-40" />
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    </SkeletonRegion>
  );
}

function DrawerBody(props: HealthFileDrawerProps & { metric: HealthDrawerMetric }) {
  const { metric, signals, lens } = props;
  const [promptOpen, setPromptOpen] = useState(false);
  const triage = useTriage(props.onFindingStatusChange);
  const model = useDrawerModel(props, triage.overrides);

  const suggestions = props.suggestions ?? {};
  const ctx: RowContext = {
    fileViewHrefFor: props.fileViewHrefFor,
    savingId: triage.savingId,
    onStatusChange: triage.setStatus,
    overrides: triage.overrides,
    detail: (f) => (
      <FindingDetail
        finding={f}
        suggestion={suggestions[f.biomarker_type]}
        onPartnerSelect={props.onPartnerSelect}
        onPartnerHref={props.onPartnerHref}
      />
    ),
  };
  const { opportunity, refactoringOpportunityHref } = props;
  const planTarget =
    opportunity && refactoringOpportunityHref
      ? refactoringOpportunityHref(opportunity.opportunity_id) || null
      : null;

  return (
    <>
      <VerdictSection
        metric={metric}
        trend={props.trend}
        breakdown={props.breakdown}
        signals={signals}
        model={model}
        planTarget={planTarget}
        fullPage={props.permalinkHref ?? props.fileViewHref}
        onNavigate={props.onNavigate}
        onPrompt={() => setPromptOpen(true)}
      />
      {lens === "performance" ? (
        <PerformanceCauses
          page={props.performance ?? null}
          loading={props.performanceLoading ?? false}
          onSelect={props.onOpportunitySelect}
        />
      ) : null}
      <WhatsWrong issues={model.topIssues} ctx={ctx} />
      <VitalSigns metric={metric} signals={signals} />
      <div className="flex flex-col">
        <OtherIssues
          rest={model.rest}
          sections={model.sections}
          leading={model.topIssues.length}
          heldBack={props.heldBack}
          heldBackTotal={model.heldBackTotal}
          ctx={ctx}
        />
        <DetailsSection metric={metric} />
        <HistorySection signals={signals} />
        <DependenciesSection signals={signals} />
        <ElsewhereSection
          related={props.related}
          relatedWorkHref={props.relatedWorkHref}
          relatedError={props.relatedError}
          onRelatedRetry={props.onRelatedRetry}
          onNavigate={props.onNavigate}
          exclude={model.relatedExclude}
          tally={model.relatedTally}
        />
        <WhyScoreSection breakdown={props.breakdown} />
      </div>
      <FilePromptModal
        open={promptOpen}
        onOpenChange={setPromptOpen}
        props={props}
        model={model}
        overrides={triage.overrides}
      />
    </>
  );
}
