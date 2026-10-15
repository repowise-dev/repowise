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
 */

import { useCallback, useId, useMemo, useState } from "react";
import { ChevronRight, ExternalLink, MoreHorizontal, Wrench } from "lucide-react";
import { AdaptivePanel } from "../shared/adaptive-panel";
import { ApiError } from "../shared/api-error";
import { InfoTip } from "../shared/info-tip";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import { toFriendlyMessage } from "../lib/errors";
import { cn } from "../lib/cn";
import {
  biomarkerLabel,
  biomarkerInfo,
  asBiomarkerDimension,
  CATEGORY_CAP,
  HISTORY_CHIP,
  HISTORY_EXPLAINER,
  HISTORY_LABEL,
  isHistoryBiomarker,
  type BiomarkerDimension,
} from "./biomarker-glossary";
import { BiomarkerDetails, type BiomarkerDetailsRecord } from "./biomarker-details";
import { ScoreBreakdown, type ScoreBreakdownCategory } from "./score-breakdown";
import { AiPromptButton } from "./ai-prompt-button";
import { AiPromptModal, fileChatContext } from "./ai-prompt-modal";
import { buildFileHealthAiPrompt } from "./ai-prompt-builder";
import { FileSignalsPanel, hasAnySignal } from "./file-signals-panel";
import {
  RelatedWork,
  relatedWorkCount,
  type RelatedLensName,
  type RelatedWorkSlotProps,
} from "./related-work";
import { CollapsibleSection } from "../shared/collapsible-section";
import { Sparkline } from "./sparkline";
import { ACTIONABILITY_LABEL } from "./performance/presentation";
import { HEALTH_UNSUPPORTED_LABEL } from "./map/lens";
import { deltaColor, formatDelta, healthBandColor, type Severity } from "./tokens";
// The shared bands, never a local threshold: this mark sits beside marks that
// all derive from `bandForScore`, and two of them disagreeing about where a
// band starts describes one file two ways in one viewport.
import { bandForScore, formatScore, HEALTH_BAND_LABEL } from "@repowise-dev/types/health";
import type {
  FileHealthTrend,
  FileSignals,
  GatedByLanguage,
  PerformanceOpportunity,
} from "@repowise-dev/types/health";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";
import { SeverityMark } from "./severity-mark";
import { VerificationTag } from "./verification-tag";
import { LowerPriorityTag } from "./lower-priority-tag";
import { PreviewBadge } from "./preview-badge";
import { ImpactFigure } from "./impact-figure";
import { fileVerdict, plainIssue } from "./plain-language";

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

/** Bucket for one-off file-level markers, kept pooled so a file with several
 *  distinct singletons still reads as one collapsed row. Not a biomarker name,
 *  and never compared against one. */
const POOLED_FILE_KEY = "__file_level__";

/** Stable empty list, so a host passing no findings does not churn the memos. */
const NO_FINDINGS: HealthDrawerFinding[] = [];

/** Findings the "What to fix" list leads with. The rest are one click away. */
const TOP_FINDINGS = 3;

const TRIAGE_STATUSES: { value: string; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "resolved", label: "Resolved" },
  { value: "false_positive", label: "False positive" },
];

const STATUS_LABEL: Record<string, string> = Object.fromEntries(
  TRIAGE_STATUSES.map((s) => [s.value, s.label]),
);

/** Closed statuses leave "What to fix": the reader already decided on them. */
const CLOSED_STATUSES = new Set(["resolved", "false_positive"]);

/** A link in ink, not accent: the accent belongs to the one primary action. */
const QUIET_LINK =
  "inline-flex w-fit items-center gap-1.5 rounded-sm text-xs font-medium text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-text-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]";

const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

/** A non-modal drawer stays open while the reader works the surface behind it. */
const keepOpen = (event: Event) => event.preventDefault();

export function HealthFileDrawer(props: HealthFileDrawerProps) {
  const { open, onClose, filePath, loading, error, onRetry, metric } = props;
  const shownPath = metric?.file_path ?? null;
  const headerPath = filePath ?? shownPath;
  // Previous data on screen under a new request: name the new file, dim the old.
  const stale =
    filePath != null && shownPath != null && filePath !== shownPath && error == null;

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
      <div
        className={cn(
          "flex flex-col gap-6 px-4 py-4 transition-opacity motion-reduce:transition-none",
          stale && "pointer-events-none opacity-60",
        )}
        aria-busy={stale || Boolean(loading && !metric)}
        // The previous file's body is a placeholder, not a surface: a triage
        // write from it would revalidate the new file's key and leave the old
        // one's cache stale.
        inert={stale}
      >
        {error != null && (!metric || metric.file_path !== headerPath) ? (
          <ApiError
            size="compact"
            title="Couldn't load this file"
            message={toFriendlyMessage(error)}
            {...(onRetry ? { onRetry } : {})}
          />
        ) : loading && !metric ? (
          <DrawerSkeleton />
        ) : !metric ? (
          <p className="text-xs text-[var(--color-text-secondary)]">
            No metric for this file yet. It appears after the next index or sync.
          </p>
        ) : (
          // Keyed by file, so triage overrides, an open prompt and every
          // disclosure start fresh on the next file rather than carrying over.
          <DrawerBody key={metric.file_path} {...props} metric={metric} />
        )}
      </div>
    </AdaptivePanel>
  );
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

function DrawerBody({
  metric,
  breakdown,
  findings: findingsProp,
  loading,
  error,
  suggestions = {},
  heldBack,
  opportunity,
  refactoringOpportunityHref,
  related,
  relatedWorkHref,
  relatedError,
  onRelatedRetry,
  onNavigate,
  trend,
  signals,
  fileViewHref,
  fileViewHrefFor,
  permalinkHref,
  onPartnerSelect,
  onPartnerHref,
  onFindingStatusChange,
  lens,
  performance,
  performanceLoading = false,
  onOpportunitySelect,
  repoName,
}: HealthFileDrawerProps & { metric: HealthDrawerMetric }) {
  const findings = findingsProp ?? NO_FINDINGS;
  // A host that has not handed findings over, or is still fetching or failed,
  // has not shown the file is clean, so the verdict claims nothing.
  const findingsKnown = findingsProp !== undefined && !loading && error == null;
  const [statusOverride, setStatusOverride] = useState<Record<string, string>>({});
  const [promptOpen, setPromptOpen] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const setStatus = useCallback(
    async (id: string, status: string) => {
      if (!onFindingStatusChange) return;
      setSavingId(id);
      try {
        await onFindingStatusChange(id, status);
        setStatusOverride((m) => ({ ...m, [id]: status }));
      } finally {
        setSavingId(null);
      }
    },
    [onFindingStatusChange],
  );
  const statusOf = (f: HealthDrawerFinding) => statusOverride[f.id] ?? f.status ?? "open";

  // What's wrong: the strongest code-shape findings still open. History
  // markers are scored but nothing in this file clears them, a closed finding
  // is one the reader already decided on, and under the performance lens its
  // findings are the causes block above, so they do not lead a second time.
  // A finding that takes nothing off the score does not lead either.
  //
  // One row per kind of problem: three "Errors caught too broadly" rows read
  // as three problems when they are one, so a kind shows its worst instance
  // and says how many more places it fires.
  const topIssues = useMemo(() => {
    const byKind = new Map<string, HealthDrawerFinding[]>();
    for (const f of findings) {
      if (
        isHistoryBiomarker(f.biomarker_type) ||
        !(f.health_impact > 0) ||
        CLOSED_STATUSES.has(statusOverride[f.id] ?? f.status ?? "open") ||
        (lens === "performance" &&
          asBiomarkerDimension(f.dimension, f.biomarker_type) === "performance")
      ) {
        continue;
      }
      const bucket = byKind.get(f.biomarker_type);
      if (bucket) bucket.push(f);
      else byKind.set(f.biomarker_type, [f]);
    }
    return [...byKind.values()]
      .map((group) => {
        const lead = group.reduce((a, b) => (b.health_impact > a.health_impact ? b : a));
        return { lead, more: group.length - 1, members: group };
      })
      .sort((a, b) => b.lead.health_impact - a.lead.health_impact)
      .slice(0, TOP_FINDINGS);
  }, [findings, statusOverride, lens]);
  const topFindings = useMemo(() => topIssues.map((i) => i.lead), [topIssues]);

  const isOpen = (f: HealthDrawerFinding) =>
    !CLOSED_STATUSES.has(statusOverride[f.id] ?? f.status ?? "open");
  // Every open finding is history: the code itself is not what scores it down.
  const historyOnly = useMemo(() => {
    const open = findings.filter(isOpen);
    return open.length > 0 && open.every((f) => isHistoryBiomarker(f.biomarker_type));
  }, [findings, statusOverride]);

  // The rest, below: a row already in What's wrong is not drawn twice. The cap
  // check still reads every finding, because a category is owned by the
  // findings on the file, not by the ones left below.
  const rest = useMemo(() => {
    const shown = new Set(topFindings.map((f) => f.id));
    return findings.filter((f) => !shown.has(f.id));
  }, [findings, topFindings]);
  const findingSections = useMemo(
    () => groupFindings(rest, breakdown?.categories ?? [], findings),
    [rest, findings, breakdown],
  );

  const verdict = useMemo(
    () =>
      fileVerdict({
        score: metric.score,
        // Every place each leading kind fires, so "two functions" counts them all.
        top: topIssues.flatMap((i) => i.members),
        hasTestFile: metric.has_test_file,
        historyOnly,
        findingsKnown,
        // What the open findings below still take off, so the sentence never
        // calls a file clean while they do.
        rest: rest.filter(isOpen).reduce(
          (acc, f) => ({
            count: acc.count + 1,
            impact: acc.impact + Math.max(0, f.health_impact),
            historyImpact:
              acc.historyImpact +
              (isHistoryBiomarker(f.biomarker_type) ? Math.max(0, f.health_impact) : 0),
          }),
          { count: 0, impact: 0, historyImpact: 0 },
        ),
      }),
    [metric.score, metric.has_test_file, topIssues, historyOnly, rest, findingsKnown, statusOverride],
  );
  const recovers = topIssueRecovery(metric, breakdown, topIssues[0]?.members);

  const heldBackTotal = useMemo(
    () => Object.values(heldBack ?? {}).reduce((n, g) => n + g.count, 0),
    [heldBack],
  );

  const planTarget =
    opportunity && refactoringOpportunityHref
      ? refactoringOpportunityHref(opportunity.opportunity_id) || null
      : null;
  const fullPage = permalinkHref ?? fileViewHref;

  const relatedExclude: readonly RelatedLensName[] =
    lens === "performance" ? ["findings", "performance"] : ["findings"];
  const relatedTally = relatedWorkCount(related, relatedExclude);

  const rowProps = {
    fileViewHrefFor,
    savingId,
    onStatusChange: onFindingStatusChange ? setStatus : undefined,
  };
  const detailFor = (f: HealthDrawerFinding) => (
    <>
      <BiomarkerDetails
        biomarkerType={f.biomarker_type}
        details={f.details}
        onPartnerSelect={onPartnerSelect}
        onPartnerHref={onPartnerHref}
      />
      {suggestions[f.biomarker_type] ? (
        <p className="text-xs text-[var(--color-text-secondary)]">
          <span className="font-medium text-[var(--color-text-primary)]">Suggested fix: </span>
          {suggestions[f.biomarker_type]}
        </p>
      ) : null}
    </>
  );

  return (
    <>
      {/* The verdict: how bad, why in one sentence, and the one thing to do. */}
      <section aria-label="Verdict" className="flex flex-col gap-3">
        {metric.score == null ? (
          // Nothing measured this file, so there is no number and no band.
          <p className="text-[15px] text-[var(--color-text-secondary)]">
            Not analysed. {HEALTH_UNSUPPORTED_LABEL}.
          </p>
        ) : (
          <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
            <ScoreLede score={metric.score} />
            {/* A flat line says nothing a "no change" would not, so it is left out. */}
            {trend &&
            trend.points.length >= 2 &&
            trend.points.some((p) => p.score !== trend.points[0]!.score) ? (
              <TrendMark trend={trend} />
            ) : null}
          </div>
        )}
        {verdict.text && (metric.score != null || findings.length > 0) ? (
          <p className="max-w-[52ch] text-[15px] leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">
            {verdict.text}
          </p>
        ) : null}
        {recovers != null ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Fixing the top issue{recovers.places > 1 ? ` in all ${recovers.places} places` : ""} is
            worth about <span className="tabular-nums">{recovers.points.toFixed(1)}</span>{" "}
            {recovers.points === 1 ? "point" : "points"}.
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-1">
          {/* The prompt is the one primary action, withheld only when the
              verdict is the all-clear and there is nothing for it to fix. */}
          {!verdict.healthy ||
          topFindings.length > 0 ||
          (lens === "performance" && (performance?.items.length ?? 0) > 0) ? (
            <AiPromptButton onClick={() => setPromptOpen(true)} label="Fix with AI prompt" />
          ) : null}
          {planTarget ? (
            <a
              href={planTarget}
              onClick={
                onNavigate
                  ? (e) => {
                      e.preventDefault();
                      onNavigate(planTarget);
                    }
                  : undefined
              }
              className={QUIET_LINK}
            >
              <Wrench className="h-3.5 w-3.5" aria-hidden="true" />
              Refactoring plan
            </a>
          ) : null}
          {fullPage ? (
            <a href={fullPage} className={QUIET_LINK}>
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              Open full page
            </a>
          ) : null}
        </div>
      </section>

      {lens === "performance" ? (
        <PerformanceCauses
          page={performance ?? null}
          loading={performanceLoading}
          onSelect={onOpportunitySelect}
        />
      ) : null}

      {topFindings.length > 0 ? (
        <section className="flex flex-col gap-1" aria-label="What's wrong">
          <h3 className={MICRO_LABEL}>What&rsquo;s wrong</h3>
          <ul className="flex flex-col">
            {topIssues.map(({ lead: f, more }) => (
              <IssueRow key={f.id} finding={f} more={more} status={statusOf(f)} {...rowProps}>
                {detailFor(f)}
              </IssueRow>
            ))}
          </ul>
        </section>
      ) : null}

      <VitalSigns metric={metric} signals={signals} />

      <div className="flex flex-col">
        {rest.length > 0 || heldBackTotal > 0 ? (
          <CollapsibleSection
            title={
              topFindings.length > 0
                ? `Other issues (${rest.length.toLocaleString()})`
                : `All issues (${rest.length.toLocaleString()})`
            }
            hint={
              topFindings.length > 0
                ? `not repeating the ${topFindings.length} above`
                : findingSections.length > 1
                  ? `in ${findingSections.length} places`
                  : undefined
            }
          >
            {heldBackTotal > 0 ? (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                <span className="tabular-nums">{heldBackTotal.toLocaleString()}</span> more{" "}
                {heldBackTotal === 1 ? "finding is" : "findings are"} held back:{" "}
                {Object.values(heldBack ?? {})
                  .map((g) => g.reason)
                  .join(" ")}
              </p>
            ) : null}
            <div className="flex flex-col">
              {findingSections.map((s) => (
                <CollapsibleSection
                  key={s.isFile ? `file:${s.key}` : `fn:${s.key}`}
                  dense
                  // Single-issue groups have nothing to collapse; the rest
                  // open as compact headers.
                  defaultOpen={s.group.length === 1}
                  title={<GroupTitle section={s} />}
                  hint={
                    <>
                      <span>
                        {s.group.length} {s.group.length === 1 ? "issue" : "issues"}
                      </span>
                      <ImpactFigure impact={s.total} tone="muted" />
                      {/* Why 34 issues cost half a point. Without this the
                          count reads as the severity and the held total looks
                          like a bug. The explanation rides in the accessible
                          name, because a bare `title` never reaches it. */}
                      {s.atCap !== null ? (
                        <span
                          className="rounded-sm border border-[var(--color-border-default)] px-1 text-[10px] text-[var(--color-text-tertiary)]"
                          aria-label={`at limit: held at its ${s.atCap.toFixed(2)}-point ceiling, so more issues of this kind take nothing further off`}
                        >
                          at limit
                        </span>
                      ) : null}
                    </>
                  }
                >
                  <ul className="flex flex-col pl-5">
                    {s.group.map((f) => (
                      <FindingRow key={f.id} finding={f} status={statusOf(f)} {...rowProps}>
                        {detailFor(f)}
                      </FindingRow>
                    ))}
                  </ul>
                </CollapsibleSection>
              ))}
            </div>
          </CollapsibleSection>
        ) : null}

        {metric.score != null || metric.nloc != null ? (
          <CollapsibleSection title="Details" hint="size, complexity, tests, module">
            <MetricGrid metric={metric} />
          </CollapsibleSection>
        ) : null}

        <HistorySection signals={signals} />

        {signals && hasAnySignal(signals, ["topology"]) ? (
          <CollapsibleSection title="Dependencies" hint={dependencyHint(signals)}>
            <FileSignalsPanel signals={signals} groups={["topology"]} hideHeading />
          </CollapsibleSection>
        ) : null}

        {relatedError != null || relatedTally.items > 0 ? (
          <CollapsibleSection
            title="Elsewhere for this file"
            hint={
              relatedError != null && relatedTally.items === 0
                ? "couldn't load"
                : `in ${relatedTally.lenses} other ${relatedTally.lenses === 1 ? "view" : "views"}`
            }
          >
            {relatedError != null && relatedTally.items === 0 ? (
              <ApiError
                size="compact"
                title="Couldn't load the other views"
                message={toFriendlyMessage(relatedError)}
                {...(onRelatedRetry ? { onRetry: onRelatedRetry } : {})}
              />
            ) : (
              <RelatedWork
                related={related}
                relatedWorkHref={relatedWorkHref}
                onNavigate={onNavigate}
                exclude={relatedExclude}
                hideHeading
              />
            )}
          </CollapsibleSection>
        ) : null}

        {/* The arithmetic behind the number at the top, categories only: the
            findings under each are already listed above. */}
        {breakdown ? (
          <CollapsibleSection title="Why this score" hint={breakdownHint(breakdown.categories)}>
            <ScoreBreakdown
              score={breakdown.score}
              totalDeduction={breakdown.total_deduction}
              categories={breakdown.categories}
              showFindings={false}
            />
          </CollapsibleSection>
        ) : null}
      </div>

      {/* Everything the drawer knows about this file, as one prompt. The modal
          renders through a portal, so it sits here for locality rather than
          for layout. */}
      <AiPromptModal
        open={promptOpen}
        onOpenChange={setPromptOpen}
        filePath={metric.file_path}
        chatContext={fileChatContext(metric.file_path)}
        title="AI prompt for this file"
        description="Every scored finding, category ceiling, open performance cause and change signal this drawer holds, written up so an agent can triage the file before it edits anything."
        getPrompt={(flavor) =>
          buildFileHealthAiPrompt({
            file: metric,
            findings: findings.map((f) => ({
              ...f,
              // Triage applied in this session but not yet refetched, so a
              // finding just marked resolved leaves the prompt.
              status: statusOverride[f.id] ?? f.status,
              details: f.details as Record<string, unknown> | null | undefined,
            })),
            categories: breakdown?.categories ?? [],
            signals: signals ?? null,
            performance: performance ?? null,
            trendDelta: trend?.delta ?? null,
            suggestions,
            flavor,
            ...(repoName ? { repoName } : {}),
          })
        }
      />
    </>
  );
}

/**
 * What fixing the top issue would give back, only when the numbers support
 * saying so: its category must not be held at a cap (removing one of many
 * capped issues changes nothing), and the score must not be clamped at the
 * floor (then the deduction overshoots what the score shows).
 */
function topIssueRecovery(
  metric: HealthDrawerMetric,
  breakdown: HealthFileDrawerProps["breakdown"],
  members: readonly HealthDrawerFinding[] | undefined,
): { points: number; places: number } | null {
  if (!members?.length || !breakdown || metric.score == null) return null;
  const cat = biomarkerInfo(members[0]!.biomarker_type).category;
  const row = breakdown.categories.find((c) => String(c.category) === cat);
  if (!row || row.capped) return null;
  if (Math.abs(10 - breakdown.total_deduction - metric.score) > 0.05) return null;
  // Every place the row covers, never more than the category actually took off
  // or than the score has room to rise.
  const sum = members.reduce((n, f) => n + Math.max(0, f.health_impact), 0);
  const gain = Math.min(sum, row.applied_deduction, 10 - metric.score);
  return gain >= 0.1 ? { points: Math.round(gain * 10) / 10, places: members.length } : null;
}

/** Direction, in neutral ink: the delta's sign carries the health colour. */
function TrendMark({ trend }: { trend: FileHealthTrend }) {
  return (
    <div className="flex items-center gap-2 pb-1 text-[var(--color-text-tertiary)]">
      <Sparkline
        values={trend.points.map((p) => p.score)}
        domain={[0, 10]}
        width={72}
        height={20}
        stroke="currentColor"
      />
      {trend.delta != null && trend.delta !== 0 ? (
        <span className={`text-xs font-semibold tabular-nums ${deltaColor(trend.delta)}`}>
          {formatDelta(trend.delta)}
        </span>
      ) : null}
      {trend.declining ? (
        <span className="text-xs text-[var(--color-text-secondary)]">declining</span>
      ) : null}
    </div>
  );
}

type Vital = { label: string; value: string; tone: "good" | "warn" | "bad" | "unknown" };

const VITAL_DOT: Record<Vital["tone"], string> = {
  good: "bg-[var(--color-success)]",
  warn: "bg-[var(--color-caution)]",
  bad: "bg-[var(--color-error)]",
  unknown: "bg-[var(--color-text-tertiary)]",
};

/**
 * Four or five facts with a dot and a word, so healthy against unhealthy reads
 * at a glance. Thresholds are the conventional ones (complexity 10 and 20, the
 * usual McCabe bands); an unmeasured fact says so in a neutral mark rather
 * than borrowing a band.
 */
function vitalSigns(m: HealthDrawerMetric, s: FileSignals | null | undefined): Vital[] {
  const out: Vital[] = [];
  // Code facts only where health measures the code; an unanalysed file keeps
  // its history facts and drops the rest, rather than "Test file: not found"
  // on a Makefile.
  const measured = m.score != null;
  if (measured && m.max_ccn != null) {
    out.push({
      label: "Complexity",
      value: m.max_ccn > 20 ? "high" : m.max_ccn > 10 ? "moderate" : "low",
      tone: m.max_ccn > 20 ? "bad" : m.max_ccn > 10 ? "warn" : "good",
    });
  }
  if (measured) {
    out.push({
      label: "Test file",
      value: m.has_test_file ? "found" : "not found",
      tone: m.has_test_file ? "good" : "warn",
    });
    out.push(
      m.line_coverage_pct == null
        ? { label: "Coverage", value: "not measured", tone: "unknown" }
        : {
            label: "Coverage",
            value: `${m.line_coverage_pct.toFixed(0)}%`,
            tone: m.line_coverage_pct >= 80 ? "good" : m.line_coverage_pct >= 50 ? "warn" : "bad",
          },
    );
  }
  if (s?.commit_count_90d != null) {
    const often = s.commit_count_90d >= 10;
    out.push({ label: "Changes often", value: often ? "yes" : "no", tone: often ? "warn" : "good" });
  }
  if (s?.prior_defect_count != null) {
    const n = s.prior_defect_count;
    out.push({
      label: "Bug fixes",
      value: n === 0 ? "none" : n >= 3 ? "frequent" : "a few",
      tone: n === 0 ? "good" : n >= 3 ? "bad" : "warn",
    });
  }
  return out;
}

function VitalSigns({
  metric,
  signals,
}: {
  metric: HealthDrawerMetric;
  signals: FileSignals | null | undefined;
}) {
  const vitals = useMemo(() => vitalSigns(metric, signals), [metric, signals]);
  if (vitals.length === 0) return null;
  return (
    <ul
      aria-label="Vital signs"
      className="flex flex-wrap gap-x-5 gap-y-2 border-t border-[var(--color-border-default)] pt-3"
    >
      {vitals.map((v) => (
        <li key={v.label} className="flex items-center gap-1.5 text-xs">
          <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${VITAL_DOT[v.tone]}`} />
          <span className="text-[var(--color-text-tertiary)]">{v.label}</span>
          <span className="font-medium text-[var(--color-text-primary)]">{v.value}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * One "What's wrong" row: a plain title, where it is, a small impact figure and
 * why it matters. Pressing the row opens the evidence and the suggested fix
 * inline; triage stays behind the row's menu.
 */
function IssueRow({
  finding: f,
  more = 0,
  status,
  fileViewHrefFor,
  savingId,
  onStatusChange,
  children,
}: {
  finding: HealthDrawerFinding;
  /** Other places the same kind of problem fires, listed under Other issues. */
  more?: number;
  status: string;
  fileViewHrefFor: ((lineStart: number) => string) | undefined;
  savingId: string | null;
  onStatusChange: ((id: string, status: string) => Promise<void>) | undefined;
  children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const plain = plainIssue(f.biomarker_type);
  const location = findingLocation(f, fileViewHrefFor);
  const saving = savingId === f.id;
  const bodyId = useId();
  return (
    <li className="border-t border-[var(--color-border-default)] first:border-t-0">
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          // Only while the body is mounted: an id that resolves to nothing is
          // a broken reference for assistive tech.
          {...(open ? { "aria-controls": bodyId } : {})}
          className="flex min-w-0 flex-1 flex-col gap-0.5 rounded-sm py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          <span className="flex min-w-0 items-baseline gap-2">
            <ChevronRight
              aria-hidden="true"
              className={cn(
                "h-3.5 w-3.5 shrink-0 self-center text-[var(--color-text-tertiary)] transition-transform motion-reduce:transition-none",
                open && "rotate-90",
              )}
            />
            <span className="min-w-0 flex-1 text-[15px] font-medium text-[var(--color-text-primary)]">
              {plain.title}
            </span>
            <ImpactFigure impact={f.health_impact} tone="muted" className="text-xs" />
          </span>
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 pl-[22px] text-xs">
            {location ? (
              <span className="font-mono text-[var(--color-text-secondary)]">{location.label}</span>
            ) : null}
            {more > 0 ? (
              <span className="text-[var(--color-text-secondary)]">
                {location ? `and ${more} more` : `in ${more + 1} places`}
              </span>
            ) : null}
            <span className="min-w-0 text-[var(--color-text-tertiary)]">{plain.why}</span>
          </span>
        </button>
        <span className="flex shrink-0 items-center gap-1 pt-2">
          {status !== "open" ? (
            <span className="rounded border border-[var(--color-border-default)] px-1.5 text-[10px] text-[var(--color-text-secondary)]">
              {STATUS_LABEL[status] ?? status}
            </span>
          ) : null}
          {saving ? <span className="text-[10px] text-[var(--color-text-tertiary)]">Saving…</span> : null}
          {onStatusChange ? (
            <TriageMenu
              label={location ? `${plain.title} at ${location.label}` : plain.title}
              status={status}
              disabled={saving}
              onSelect={(next) => void onStatusChange(f.id, next).catch(() => {})}
            />
          ) : null}
        </span>
      </div>
      {open ? (
        <div id={bodyId} className="flex flex-col gap-2 pb-3 pl-[22px]">
          <p className="text-xs text-[var(--color-text-secondary)]">
            <span className="font-medium text-[var(--color-text-primary)]">
              {biomarkerLabel(f.biomarker_type)}.
            </span>{" "}
            {f.reason}
          </p>
          <span className="flex flex-wrap gap-1.5 empty:hidden">
            <VerificationTag verification={f.verification} />
            <LowerPriorityTag reason={f.lower_priority} />
            <PreviewBadge note={f.preview} />
          </span>
          {location?.href ? (
            <a href={location.href} className={cn(QUIET_LINK, "font-mono")}>
              Go to {location.label}
            </a>
          ) : null}
          {children}
        </div>
      ) : null}
    </li>
  );
}

interface FindingSection {
  key: string;
  group: HealthDrawerFinding[];
  isFile: boolean;
  total: number;
  worst: HealthDrawerFinding;
  /** The enforced cap when this group's category is holding at it, else null. */
  atCap: number | null;
}

/**
 * Group findings by the function they fire on, so one oversized function reads
 * as a single collapsible group instead of N sibling rows.
 *
 * A file-level biomarker gets its own group only when it actually repeats:
 * `error_handling` sets no function_name and fires once per occurrence, so one
 * file in this repo reaches 34 sibling rows worth 0.51 points. Splitting every
 * file-level biomarker out was measurably worse (53% of files carry 2+
 * distinct one-off file-level markers), so the one-offs stay pooled.
 */
function groupFindings(
  findings: HealthDrawerFinding[],
  categories: ScoreBreakdownCategory[],
  /** Every finding on the file, for the cap's ownership test. */
  allFindings: HealthDrawerFinding[] = findings,
): FindingSection[] {
  // Categories the scorer held at their ceiling, per the server breakdown.
  // `capped` is the server's verdict and is never recomputed here; the cap
  // VALUE is display only, so it falls back to the glossary mirror.
  const cappedCategories = new Map<string, number | null>();
  for (const c of categories) {
    if (c.capped) cappedCategories.set(String(c.category), c.cap ?? null);
  }

  const byFunction = new Map<string, HealthDrawerFinding[]>();
  const byBiomarker = new Map<string, HealthDrawerFinding[]>();
  for (const f of findings) {
    // Two maps, never one keyed by a sentinel string: a C++ or Rust function
    // can legitimately be `file::read`, so any "file" prefix is collidable.
    const map = f.function_name ? byFunction : byBiomarker;
    const key = f.function_name ?? f.biomarker_type;
    const bucket = map.get(key);
    if (bucket) bucket.push(f);
    else map.set(key, [f]);
  }

  const pooled: HealthDrawerFinding[] = [];
  const fileGroups: { key: string; group: HealthDrawerFinding[] }[] = [];
  for (const [key, group] of byBiomarker) {
    if (group.length > 1) fileGroups.push({ key, group });
    else pooled.push(...group);
  }
  if (pooled.length > 0) fileGroups.push({ key: POOLED_FILE_KEY, group: pooled });

  const sections = [
    ...[...byFunction.entries()].map(([key, group]) => ({ key, group, isFile: false })),
    ...fileGroups.map(({ key, group }) => ({ key, group, isFile: true })),
  ];

  return sections
    .map(({ key, group, isFile }) => {
      // Inside a single-biomarker group every marker carries the same label
      // and impact, so the line is the only axis a reader can follow.
      if (isFile && key !== POOLED_FILE_KEY) {
        group = [...group].sort(
          (a, b) => (a.line_start ?? Infinity) - (b.line_start ?? Infinity),
        );
      }
      const total = group.reduce((s, f) => s + f.health_impact, 0);
      const worst = group.reduce((a, b) => (b.health_impact > a.health_impact ? b : a));
      // Claim the cap only when this group IS the category, decided from the
      // findings on screen: a host may scope them differently from the
      // server's subtotal (hosted excludes triaged findings from one).
      const cat = biomarkerInfo(worst.biomarker_type).category;
      const ownsCategory =
        key !== POOLED_FILE_KEY &&
        allFindings.every(
          (f) =>
            biomarkerInfo(f.biomarker_type).category !== cat ||
            f.biomarker_type === worst.biomarker_type,
        );
      const atCap =
        isFile && ownsCategory && cappedCategories.has(cat)
          ? (cappedCategories.get(cat) ?? CATEGORY_CAP[cat] ?? null)
          : null;
      return { key, group, isFile, total, worst, atCap };
    })
    .sort((a, b) => b.total - a.total);
}

/** A group's header: the function plus its worst marker, or the file-level biomarker. */
function GroupTitle({ section: s }: { section: FindingSection }) {
  const worstLabel = biomarkerLabel(s.worst.biomarker_type);
  if (!s.isFile) {
    return (
      <span className="block truncate text-[var(--color-text-primary)]">
        <span className="font-mono">{s.key}</span>
        <span className="text-[var(--color-text-tertiary)]"> · {worstLabel}</span>
      </span>
    );
  }
  return (
    <span className="block truncate text-[var(--color-text-primary)]">
      {s.key === POOLED_FILE_KEY ? (
        "Across the file"
      ) : (
        <>
          {worstLabel}
          <span className="text-[var(--color-text-tertiary)]"> · across the file</span>
        </>
      )}
    </span>
  );
}

/** Where a finding fires, and the link to it when the host has one. */
function findingLocation(
  f: HealthDrawerFinding,
  fileViewHrefFor: ((lineStart: number) => string) | undefined,
): { label: string; href: string | undefined } | null {
  // Anchored on line_start, not function_name: `error_handling` fires per
  // occurrence with a precise line and no function, and withholding the line
  // made its rows read as duplicates.
  if (!f.function_name && f.line_start == null) return null;
  const label = f.function_name
    ? `${f.function_name}${f.line_start != null ? `:${f.line_start}` : ""}`
    : `line ${f.line_start}`;
  // A link only when it goes to the line. Falling back to the file page made
  // every row link to one page, which "Open full page" already is.
  const href =
    f.line_start != null && fileViewHrefFor ? fileViewHrefFor(f.line_start) : undefined;
  return { label, href };
}

/**
 * One finding as a hairline row. `compact` is the "What to fix" form: label,
 * location, impact and the reason on one line. The full form adds whatever
 * `children` carries (evidence, suggestion, plan link).
 *
 * Chips mark exceptions only: a provisional type, a lower-priority reason, a
 * preview cell, a history marker, a triaged status. Every row carrying its
 * category and pillar communicated nothing.
 */
function FindingRow({
  finding: f,
  status,
  compact = false,
  fileViewHrefFor,
  savingId,
  onStatusChange,
  children,
}: {
  finding: HealthDrawerFinding;
  status: string;
  compact?: boolean;
  fileViewHrefFor: ((lineStart: number) => string) | undefined;
  savingId: string | null;
  onStatusChange: ((id: string, status: string) => Promise<void>) | undefined;
  children?: React.ReactNode;
}) {
  const info = biomarkerInfo(f.biomarker_type);
  const label = biomarkerLabel(f.biomarker_type);
  const location = findingLocation(f, fileViewHrefFor);
  const saving = savingId === f.id;
  const locationNode = location ? (
    location.href ? (
      <a href={location.href} className={cn(QUIET_LINK, "shrink-0 font-mono font-normal")}>
        {location.label}
      </a>
    ) : (
      <span className="shrink-0 font-mono text-xs text-[var(--color-text-tertiary)]">
        {location.label}
      </span>
    )
  ) : null;

  return (
    <li className="flex flex-col gap-1 border-t border-[var(--color-border-default)] py-2.5 first:border-t-0">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <SeverityMark severity={f.severity} compact={compact} />
        <span className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--color-text-primary)]">
          {label}
          {info.description ? <InfoTip content={info.description} label={`About ${label}`} /> : null}
        </span>
        <VerificationTag verification={f.verification} />
        <LowerPriorityTag reason={f.lower_priority} />
        <PreviewBadge note={f.preview} />
        {isHistoryBiomarker(f.biomarker_type) ? (
          // Scored, but nothing in this file will clear it.
          <span
            className={`inline-flex items-center gap-1 rounded px-1.5 py-px text-[10px] font-medium ${HISTORY_CHIP}`}
          >
            {HISTORY_LABEL}
            <InfoTip content={HISTORY_EXPLAINER} label="Why this is a watch item" />
          </span>
        ) : null}
        {status !== "open" ? (
          <span className="rounded border border-[var(--color-border-default)] px-1.5 text-[10px] text-[var(--color-text-secondary)]">
            {STATUS_LABEL[status] ?? status}
          </span>
        ) : null}
        {compact ? null : locationNode}
        <span className="ml-auto inline-flex shrink-0 items-center gap-1">
          {saving ? (
            <span className="text-[10px] text-[var(--color-text-tertiary)]">Saving…</span>
          ) : null}
          <ImpactFigure impact={f.health_impact} tone="muted" className="text-xs" />
          {onStatusChange ? (
            <TriageMenu
              label={location ? `${label} at ${location.label}` : label}
              status={status}
              disabled={saving}
              onSelect={(next) => void onStatusChange(f.id, next).catch(() => {})}
            />
          ) : null}
        </span>
      </div>
      {compact ? (
        <div className="flex min-w-0 items-baseline gap-2">
          {locationNode}
          {/* One line here; the full reason is still in the accessible text
              and opens in All findings. */}
          <p className="min-w-0 truncate text-xs text-[var(--color-text-secondary)]" title={f.reason}>
            {f.reason}
          </p>
        </div>
      ) : (
        <p className="text-xs text-[var(--color-text-secondary)]">{f.reason}</p>
      )}
      {children}
    </li>
  );
}

/** Triage behind one overflow control, so a row has one visible verb at most. */
function TriageMenu({
  label,
  status,
  disabled,
  onSelect,
}: {
  label: string;
  status: string;
  disabled: boolean;
  onSelect: (status: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const headingId = useId();
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={`Triage ${label}`}
        disabled={disabled}
        className="rounded-md p-1 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-selected)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] disabled:opacity-50"
      >
        <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-44 p-1">
        {/* A labelled group of toggle buttons, not a menu: it has no roving
            focus, so it does not claim the menu keyboard contract. Tab moves
            through the options and Escape closes the popover. */}
        <div role="group" aria-labelledby={headingId}>
          <p
            id={headingId}
            className="px-2 pb-1 pt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
          >
            Mark as
          </p>
          {TRIAGE_STATUSES.map((opt) => {
            const current = opt.value === status;
            return (
              <button
                key={opt.value}
                type="button"
                aria-pressed={current}
                disabled={current}
                onClick={() => {
                  setOpen(false);
                  onSelect(opt.value);
                }}
                className={cn(
                  "block w-full rounded px-2 py-1.5 text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]",
                  current
                    ? "font-medium text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-selected)] hover:text-[var(--color-text-primary)]",
                )}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}

/**
 * Process and people signals plus where the bug fixes landed, as one section:
 * the fix count and its recency live in the process row, so the per-symbol
 * list under it does not repeat them.
 *
 * The per-symbol counts are labelled approximate, because symbol spans are
 * current-tree while each fix's line ranges are numbered on its own parent
 * commit. No commit is named: file-level SZZ ran at 74.5% precision, enough to
 * count fixes and not enough to say which commit caused one.
 */
function HistorySection({ signals }: { signals: FileSignals | null | undefined }) {
  if (!signals) return null;
  const counts = signals.fix_symbol_counts;
  const hasFixes = Boolean(counts && Object.keys(counts).length > 0);
  if (!hasFixes && !hasAnySignal(signals, ["process", "people"])) return null;
  const entries = hasFixes ? Object.entries(counts!).sort((a, b) => b[1] - a[1]) : [];

  return (
    <CollapsibleSection title="History & ownership" hint={historyHint(signals)}>
      <FileSignalsPanel signals={signals} groups={["process", "people"]} hideHeading />
      {hasFixes ? (
        <div className="flex flex-col gap-1.5 border-t border-[var(--color-border-default)] pt-2.5">
          <p className={MICRO_LABEL}>Where the fixes landed</p>
          <ul className="space-y-1">
            {entries.map(([symbolId, count]) => (
              <li
                key={symbolId}
                className="flex items-baseline gap-2 text-xs text-[var(--color-text-secondary)]"
              >
                <code className="font-mono text-[var(--color-text-primary)]">
                  {symbolId.split("::").pop()}
                </code>
                <span className="ml-auto tabular-nums text-[var(--color-text-tertiary)]">
                  {count} {count === 1 ? "fix" : "fixes"}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-[10px] leading-tight text-[var(--color-text-tertiary)]">
            Approximate: fixes are matched to symbols by line range, and lines move.
          </p>
        </div>
      ) : null}
    </CollapsibleSection>
  );
}

/**
 * What the history section holds, in words. Its counts are the body's
 * headline, so the hint names the kinds of evidence rather than restating them.
 */
function historyHint(s: FileSignals): string | undefined {
  const parts: string[] = [];
  if (s.prior_defect_count != null || s.commit_count_90d != null || s.age_days != null) {
    parts.push("change history");
  }
  if (s.primary_owner_name != null || s.recent_owner_name != null) parts.push("owners");
  if (s.fix_symbol_counts && Object.keys(s.fix_symbol_counts).length > 0) {
    parts.push("where fixes landed");
  }
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

/** One plain fact about the file's place in the graph; the body has the counts. */
function dependencyHint(s: FileSignals): string {
  if (s.in_degree == null) return "what it depends on";
  if (s.in_degree === 0) return "nothing depends on it";
  return s.out_degree === 0 ? "others depend on it, it depends on nothing" : "others depend on it";
}

/** The total is the body's arithmetic line, so the hint names the categories. */
function breakdownHint(categories: ScoreBreakdownCategory[]): string {
  const n = categories.length;
  const held = categories.filter((c) => c.capped).length;
  const base = `${n} ${n === 1 ? "category" : "categories"}`;
  return held > 0 ? `${base}, ${held} at ${held === 1 ? "its" : "their"} limit` : base;
}

/** A structural counter that may genuinely be unmeasured: say so instead of
 *  rendering a misleading 0. */
function MeasuredNum({ v }: { v: number | null }) {
  if (v == null) {
    return (
      <span
        className="text-xs text-[var(--color-text-tertiary)]"
        title="Not measured: no metric row is available for this file on this snapshot."
      >
        not measured
      </span>
    );
  }
  return <span className="text-[15px] font-semibold tabular-nums">{v}</span>;
}

/** The score in ink and its band as a dot and a word: the dot is the health colour. */
function ScoreLede({ score }: { score: number }) {
  const color = healthBandColor(bandForScore(score));
  return (
    <>
      <div className="flex items-baseline gap-x-2">
        <span className="text-[52px] font-semibold leading-none tracking-tight tabular-nums text-[var(--color-text-primary)]">
          {formatScore(score)}
        </span>
        <span className="text-xs text-[var(--color-text-tertiary)]">out of 10</span>
      </div>
      <span className="inline-flex items-center gap-1.5 pb-1 text-[15px] font-medium text-[var(--color-text-primary)]">
        <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: color }} />
        {HEALTH_BAND_LABEL[bandForScore(score)]}
      </span>
    </>
  );
}

/**
 * The two co-pillars and the structural counters, as one hairline `<dl>`, in
 * the words a developer uses, with the term of art one hover away. Scores
 * carry their band colour; counters are plain, because a nesting depth of 3 is
 * not good or bad news on its own.
 */
function MetricGrid({ metric }: { metric: HealthDrawerMetric }) {
  const cells: { label: string; tip?: string; value: React.ReactNode }[] = [
    {
      label: "Maintainability",
      value: <PillarScore v={metric.maintainability_score ?? null} />,
    },
    { label: "Performance", value: <PillarScore v={metric.performance_score ?? null} /> },
    {
      // "Line coverage", not "Tests": the paired-file flag below is the tests
      // cell, and two cells named alike in one grid is the vocabulary failure.
      label: "Line coverage",
      tip: "Share of the file's lines a coverage report saw run.",
      value: (
        <PlainValue>
          {metric.line_coverage_pct == null
            ? "not measured"
            : `${metric.line_coverage_pct.toFixed(0)}%`}
        </PlainValue>
      ),
    },
    {
      label: "Complexity",
      tip: "Cyclomatic complexity (CCN) of the most complex function: the number of independent paths through it.",
      value: <MeasuredNum v={metric.max_ccn} />,
    },
    {
      label: "Nesting depth",
      tip: "The deepest level of nested blocks in any function.",
      value: <MeasuredNum v={metric.max_nesting} />,
    },
    {
      label: "Lines of code",
      tip: "Non-blank, non-comment lines (NLOC).",
      value: <MeasuredNum v={metric.nloc} />,
    },
    {
      label: "Duplication",
      tip: "Share of lines that repeat a block found elsewhere.",
      value: (
        <PlainValue>
          {metric.duplication_pct == null
            ? "not measured"
            : `${metric.duplication_pct.toFixed(0)}%`}
        </PlainValue>
      ),
    },
    {
      label: "Test file",
      value: <PlainValue>{metric.has_test_file ? "Paired" : "None"}</PlainValue>,
    },
    {
      label: "Module",
      value: (
        <span
          className="block truncate font-mono text-xs text-[var(--color-text-primary)]"
          title={metric.module ?? undefined}
        >
          {metric.module ?? "none"}
        </span>
      ),
    },
  ];

  return (
    <dl className="grid grid-cols-2 border-y border-[var(--color-border-default)] sm:grid-cols-3">
      {cells.map((c, i) => (
        <div
          key={c.label}
          className={[
            "min-w-0 px-3 py-2.5",
            "border-[var(--color-border-default)]",
            // Hairlines between cells only; the outer edges come from border-y
            // on the wrapper, so cells never double up on a boundary.
            i % 2 === 1 ? "border-l" : "",
            i >= 2 ? "border-t" : "",
            "sm:border-l sm:border-t-0",
            i % 3 === 0 ? "sm:border-l-0" : "",
            i >= 3 ? "sm:border-t" : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          <dt className={`inline-flex items-center gap-1 ${MICRO_LABEL}`}>
            <span>{c.label}</span>
            {c.tip ? <InfoTip content={c.tip} label={`About ${c.label.toLowerCase()}`} /> : null}
          </dt>
          <dd className="mt-1">{c.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A 0-10 pillar score, band-coloured, or an honest "not measured". */
function PillarScore({ v }: { v: number | null }) {
  if (v == null) {
    return <span className="text-xs text-[var(--color-text-tertiary)]">not measured</span>;
  }
  return (
    <span
      className="text-[18px] font-semibold tabular-nums"
      style={{ color: healthBandColor(bandForScore(v)) }}
    >
      {formatScore(v)}
      <span className="text-xs font-normal text-[var(--color-text-tertiary)]">/10</span>
    </span>
  );
}

function PlainValue({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[15px] tabular-nums text-[var(--color-text-primary)]">{children}</span>
  );
}

/**
 * The file's open performance causes, when the reader arrived from that lens.
 *
 * Counts and causes, not a score: the performance pillar compresses into a
 * narrow band and a number from it says nothing about this file.
 *
 * A cause is titled by the shape the detector recognized and identified by
 * where it fires, because on one file that is what tells two of them apart:
 * this repository has files carrying thirty-six causes of a single marker. The
 * section says plainly when the host never asked for the data, because an
 * empty list would read as a clear file.
 */
function PerformanceCauses({
  page,
  loading,
  onSelect,
}: {
  page: { items: PerformanceOpportunity[]; total: number } | null;
  loading: boolean;
  onSelect?: ((opportunityId: string) => void) | undefined;
}) {
  const items = page?.items ?? [];
  const withPlan = items.filter((o) => o.plan_id).length;
  return (
    <section className="flex flex-col gap-2">
      <h3 className={MICRO_LABEL}>Performance causes</h3>
      {loading ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">Loading causes…</p>
      ) : page === null ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          This view has no performance data wired up, so nothing is claimed about this
          file either way.
        </p>
      ) : items.length === 0 ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          No open cause names this file as the place to intervene. The detectors are
          high precision and low recall, so that is not a measurement that it is fast.
        </p>
      ) : (
        <>
          <p className="text-xs text-[var(--color-text-secondary)]">
            <span className="tabular-nums">{page.total.toLocaleString()}</span> open cause
            {page.total === 1 ? "" : "s"} name this file as the place to intervene
            {items.length < page.total ? (
              <>
                , <span className="tabular-nums">{items.length}</span> shown
              </>
            ) : null}
            . <span className="tabular-nums">{withPlan}</span> of those carry a stored plan.
          </p>
          <ul className="flex flex-col divide-y divide-[var(--color-border-default)]">
            {items.map((o) => (
              <CauseRow key={o.opportunity_id} opportunity={o} onSelect={onSelect} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** Where a cause fires, from the evidence the queue already carries. */
function causeLocation(o: PerformanceOpportunity): string | null {
  if (o.terminal_sink) return o.terminal_sink;
  if (o.intervention_symbol) return o.intervention_symbol;
  const first = o.evidence[0];
  if (!first) return null;
  const name = first.function_name ?? null;
  if (name && first.line_start != null) return `${name}:${first.line_start}`;
  if (name) return name;
  return first.line_start != null ? `line ${first.line_start}` : null;
}

function CauseRow({
  opportunity: o,
  onSelect,
}: {
  opportunity: PerformanceOpportunity;
  onSelect?: ((opportunityId: string) => void) | undefined;
}) {
  const location = causeLocation(o);
  const body = (
    <>
      <span className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 text-xs font-medium text-[var(--color-text-primary)]">
          {biomarkerLabel(o.biomarker_type)}
        </span>
        <span className="shrink-0 font-mono text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
          {ACTIONABILITY_LABEL[o.actionability_state]}
        </span>
      </span>
      {location ? (
        <span className="truncate font-mono text-[10px] text-[var(--color-text-secondary)]">
          {location}
        </span>
      ) : null}
      <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {o.observations_total} observation{o.observations_total === 1 ? "" : "s"} ·{" "}
        {o.affected_files_total} file{o.affected_files_total === 1 ? "" : "s"} ·{" "}
        {o.plan_id ? "stored plan" : o.plan_reason}
      </span>
    </>
  );
  return (
    <li>
      {onSelect ? (
        <button
          type="button"
          onClick={() => onSelect(o.opportunity_id)}
          className="flex w-full flex-col gap-0.5 px-1 py-2 text-left transition-colors hover:bg-[var(--color-bg-selected)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          {body}
        </button>
      ) : (
        <div className="flex flex-col gap-0.5 px-1 py-2">{body}</div>
      )}
    </li>
  );
}
