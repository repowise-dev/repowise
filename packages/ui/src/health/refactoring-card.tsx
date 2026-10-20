"use client";

import { useState } from "react";
import { ArrowUpRight, ChevronDown, ChevronRight } from "lucide-react";
import { InfoTip } from "../shared/info-tip";
import { FilePath } from "../shared/file-path";
import { hairlineCardClass } from "../shared/hairline-card";
import {
  biomarkerInfo,
  biomarkerLabel,
  HISTORY_EXPLAINER,
  HISTORY_LABEL,
  isWatchOnlyBiomarker,
} from "./biomarker-glossary";
import type { BiomarkerDetailsRecord } from "./biomarker-details";
import { MICRO, type Severity } from "./tokens";
import { EFFORT_LABEL } from "./labels";
import { ImpactFigure } from "./impact-figure";
import { FindingOpportunityLink } from "./file-opportunity";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";
import { AskAboutThis } from "../chat/ask-about-this";
import { SeverityMark } from "./severity-mark";
import { VerificationTag } from "./verification-tag";
import { LowerPriorityTag } from "./lower-priority-tag";
import { PreviewBadge } from "./preview-badge";
import { AiPromptButton } from "./ai-prompt-button";
import { AGENT_PROMPT_LABEL } from "./ai-prompt-action";
import { cn } from "../lib/cn";

export type EffortBucket = "S" | "M" | "L" | "XL";

export interface HealthWorkItemFinding {
  id: string;
  biomarker_type: string;
  severity: Severity;
  function_name: string | null;
  line_start?: number | null;
  line_end?: number | null;
  health_impact: number;
  reason: string;
  status?: string;
  details?: BiomarkerDetailsRecord | null;
  /** `"unverified"` for a provisional finding type. */
  verification?: string | null;
  lower_priority?: string | null;
  /** The measurement note when the row's language cell is served as a preview;
   *  null or absent for a validated cell. */
  preview?: string | null;
}

export interface HealthWorkItem {
  file_path: string;
  score: number;
  nloc: number;
  module?: string | null;
  /** A test file; labelled on the card. */
  is_test?: boolean;
  primary_biomarker: string;
  primary_severity: Severity;
  primary_reason: string;
  primary_function: string | null;
  primary_line_start: number | null;
  primary_line_end: number | null;
  primary_suggestion?: string;
  primary_finding_id?: string;
  total_impact: number;
  finding_count: number;
  open_finding_count?: number;
  biomarkers: string[];
  effort_bucket: EffortBucket;
  impact_per_effort: number;
  all_findings?: HealthWorkItemFinding[];
  /** The measurement note when the row's language cell is served as a preview;
   *  null or absent for a validated cell. */
  preview?: string | null;
}

export type FindingStatus = "open" | "acknowledged" | "resolved" | "false_positive";

export interface HealthWorkItemCardProps {
  target: HealthWorkItem;
  onSelect?: ((target: HealthWorkItem) => void) | undefined;
  onStatusChange?: ((findingId: string, status: FindingStatus) => void) | undefined;
  onGeneratePrompt?: ((target: HealthWorkItem) => void) | undefined;
  /** This file's composed refactoring opportunity, if the host already has one. */
  opportunity?: RefactoringOpportunity | null | undefined;
  /**
   * Resolve this file's opportunity on first expand, alongside the findings.
   * Lazy for the same reason they are: a queue of hundreds of collapsed cards
   * should not fetch a plan for every one of them.
   */
  onLoadOpportunity?:
    | ((filePath: string) => Promise<RefactoringOpportunity | null>)
    | undefined;
  refactoringOpportunityHref?: ((opportunityId: string) => string) | undefined;
  /**
   * Fetch this file's findings, called on first expand. The list response
   * deliberately omits them — serializing every file's findings to render a
   * list that shows none of them cost 1.8 MB per request. Falls back to
   * `target.all_findings` when the host does not supply this, so a card fed by
   * an older payload still expands.
   */
  onLoadFindings?:
    | ((filePath: string) => Promise<HealthWorkItemFinding[]>)
    | undefined;
  expandable?: boolean;
  /** Flash-highlight the card (e.g. after a quadrant dot click scrolled to it). */
  highlighted?: boolean;
  /** Bulk triage: whether this row is in the selection. */
  selected?: boolean;
  /**
   * Toggle this row in the bulk selection. The row stands for the finding it
   * names (`primary_finding_id`), so a row without one offers no checkbox.
   */
  onToggleSelect?: ((target: HealthWorkItem) => void) | undefined;
}

export function HealthWorkItemCard({
  target,
  onSelect,
  onStatusChange,
  onGeneratePrompt,
  opportunity,
  refactoringOpportunityHref,
  onLoadOpportunity,
  onLoadFindings,
  expandable = true,
  highlighted = false,
  selected = false,
  onToggleSelect,
}: HealthWorkItemCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [loaded, setLoaded] = useState<HealthWorkItemFinding[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadedOpportunity, setLoadedOpportunity] = useState<
    RefactoringOpportunity | null | undefined
  >(undefined);
  const shownOpportunity = opportunity ?? loadedOpportunity;

  // `finding_count` is on every target, so the expander's presence and its
  // label no longer depend on shipping the findings themselves.
  const findings = target.all_findings ?? loaded;
  const hasFindings = target.finding_count > 0;
  // Led by a history marker: context for a reviewer, not something an edit
  // clears, so the card neither rates it as a defect nor offers a fix prompt.
  const watch = isWatchOnlyBiomarker(target.primary_biomarker);

  const toggle = async () => {
    const next = !expanded;
    setExpanded(next);
    if (!next) return;
    if (onLoadOpportunity && opportunity === undefined && loadedOpportunity === undefined) {
      // A failure here stays `undefined` rather than becoming `null`: the card
      // must not claim a file has no plan when the lookup is what failed.
      void onLoadOpportunity(target.file_path)
        .then((result) => setLoadedOpportunity(result ?? null))
        .catch(() => undefined);
    }
    if (findings || !onLoadFindings) return;
    setLoadFailed(false);
    try {
      setLoaded(await onLoadFindings(target.file_path));
    } catch {
      // Keep the card usable: the header already carries the primary finding.
      setLoadFailed(true);
    }
  };
  return (
    <div
      data-health-work-item={target.file_path}
      data-highlighted={highlighted || undefined}
      className={cn("overflow-hidden", hairlineCardClass("group", { highlighted }))}
    >
      <div className="p-4 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          {onToggleSelect && target.primary_finding_id ? (
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onToggleSelect(target)}
              aria-label={`Select the ${biomarkerLabel(target.primary_biomarker)} finding in ${target.file_path}`}
              className="h-3.5 w-3.5 rounded border-[var(--color-border-default)] accent-[var(--color-accent-primary)]"
            />
          ) : null}
          {watch ? (
            <span className={MICRO}>{HISTORY_LABEL}</span>
          ) : (
            <SeverityMark severity={target.primary_severity} />
          )}
          <span className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-text-primary)]">
            {biomarkerLabel(target.primary_biomarker)}
            {biomarkerInfo(target.primary_biomarker).description ? (
              <InfoTip
                content={biomarkerInfo(target.primary_biomarker).description}
                label={`About ${biomarkerLabel(target.primary_biomarker)}`}
              />
            ) : null}
          </span>
          {target.module ? (
            <span className="text-xs text-[var(--color-text-tertiary)]">{target.module}</span>
          ) : null}
          {target.is_test ? <span className={MICRO}>test</span> : null}
          <PreviewBadge note={target.preview} />
          <AskAboutThis
            context={{
              kind: "health",
              label: target.file_path,
              target: target.file_path,
              targetKind: "path",
            }}
            question={
              watch
                ? `What does the ${biomarkerLabel(target.primary_biomarker)} signal say about ${target.file_path}, and what should a reviewer watch for when it changes?`
                : `Explain the ${biomarkerLabel(target.primary_biomarker)} finding in ${target.file_path} and propose a safe way to address it.`
            }
            label={`Ask about the finding in ${target.file_path}`}
            className="-my-1 ml-auto h-6 w-6"
          />
        </div>
        <button
          type="button"
          data-work-item-header=""
          onClick={onSelect ? () => onSelect(target) : undefined}
          className="group/file flex w-full items-center gap-1.5 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] disabled:cursor-default"
          disabled={!onSelect}
          title={onSelect ? "Open file health drawer" : undefined}
        >
          {/* The path once, name first; the symbol after it in the same mono. */}
          <p className="flex min-w-0 flex-1 items-baseline font-mono text-sm text-[var(--color-text-primary)]">
            <FilePath path={target.file_path} fold className="text-sm" />
            {target.primary_function ? (
              <span className="min-w-0 truncate whitespace-pre text-[var(--color-text-secondary)]">
                {" :: "}
                {target.primary_function}
              </span>
            ) : null}
          </p>
          {onSelect ? (
            <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] transition-transform group-hover/file:-translate-y-px group-hover/file:translate-x-px group-hover/file:text-[var(--color-text-secondary)] group-focus-visible/file:text-[var(--color-text-secondary)]" />
          ) : null}
        </button>
        <p className="text-xs text-[var(--color-text-secondary)] line-clamp-2">{target.primary_reason}</p>
        {/* The action comes from core, per marker; the card never writes one. */}
        {target.primary_suggestion ? (
          <p className="text-xs text-[var(--color-text-secondary)] line-clamp-3">
            <span className={cn(MICRO, "mr-1.5")}>Action</span>
            {target.primary_suggestion}
          </p>
        ) : null}
        {watch ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">{HISTORY_EXPLAINER}</p>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 pt-1">
          <WorkItemMeta target={target} />
          {onGeneratePrompt && !watch ? (
            <AiPromptButton
              variant="ghost"
              label={AGENT_PROMPT_LABEL}
              ariaLabel={`${AGENT_PROMPT_LABEL}: ${target.file_path}`}
              onClick={() => onGeneratePrompt(target)}
              className="-mr-2"
            />
          ) : null}
        </div>
      </div>
      {expandable && hasFindings ? (
        <>
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="flex w-full items-center gap-2 border-t border-[var(--color-border-default)] px-4 py-2 text-xs text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-accent-primary)]"
          >
            {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            {expanded ? "Hide" : "Show"} all {target.finding_count} findings
          </button>
          {expanded && !findings ? (
            <p className="border-t border-[var(--color-border-default)] px-4 py-2 text-xs text-[var(--color-text-tertiary)]">
              {loadFailed ? "Could not load findings." : "Loading findings…"}
            </p>
          ) : null}
          {expanded && findings ? (
            <ul className="divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)]">
              {findings.map((f) => (
                <li key={f.id} className="px-4 py-2 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <SeverityMark severity={f.severity} />
                    <span className="text-xs font-medium text-[var(--color-text-primary)]">
                      {biomarkerLabel(f.biomarker_type)}
                    </span>
                    <VerificationTag verification={f.verification} />
                    <LowerPriorityTag reason={f.lower_priority} />
                    <PreviewBadge note={f.preview} />
                    {f.function_name ? (
                      <span className="text-xs font-mono text-[var(--color-text-tertiary)]">{f.function_name}</span>
                    ) : null}
                    <FindingLine lineStart={f.line_start} />
                    <ImpactFigure impact={f.health_impact} className="ml-auto text-xs" />
                  </div>
                  <p className="text-xs text-[var(--color-text-tertiary)] line-clamp-2">{f.reason}</p>
                  <FindingOpportunityLink
                    opportunity={shownOpportunity}
                    biomarkerType={f.biomarker_type}
                    href={refactoringOpportunityHref}
                  />
                  {onStatusChange ? (
                    <div className="flex flex-wrap gap-1 pt-1">
                      <StatusButton current={f.status} value="acknowledged" onClick={() => onStatusChange(f.id, "acknowledged")} label="Acknowledge" />
                      <StatusButton current={f.status} value="resolved" onClick={() => onStatusChange(f.id, "resolved")} label="Resolved" />
                      <StatusButton current={f.status} value="false_positive" onClick={() => onStatusChange(f.id, "false_positive")} label="False positive" />
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/** @deprecated Health queue compatibility names; these are not structured plans. */
export type RefactoringTarget = HealthWorkItem;
export type RefactoringTargetFinding = HealthWorkItemFinding;
export type RefactoringCardProps = HealthWorkItemCardProps;
export const RefactoringCard = HealthWorkItemCard;

/**
 * The decision facts on one quiet line: score, effort, how many findings.
 * Impact, leverage and size order the list but do not decide a row, so they
 * sit behind the tip.
 */
function WorkItemMeta({ target }: { target: HealthWorkItem }) {
  const findings =
    target.open_finding_count != null && target.open_finding_count !== target.finding_count
      ? `${target.open_finding_count} open of ${target.finding_count} findings`
      : `${target.finding_count} findings`;
  return (
    <p className="flex flex-wrap items-center gap-x-1.5 font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
      <span>Score {target.score.toFixed(1)}/10</span>
      <span aria-hidden>·</span>
      <span>{EFFORT_LABEL[target.effort_bucket]} effort</span>
      <span aria-hidden>·</span>
      <span>{findings}</span>
      <InfoTip
        label="How this file ranks"
        content={`Health impact −${target.total_impact.toFixed(2)} across its findings, over ${target.nloc} lines of code. Leverage ${target.impact_per_effort.toFixed(2)}: impact per unit of effort, which orders this list.`}
      />
    </p>
  );
}

/**
 * Where a finding sits. Not a link: no file view renders a line anchor, so a
 * "line 412" href would land at the top of the page every time. Silent
 * without a line, because a marker describing the whole file has none.
 */
function FindingLine({ lineStart }: { lineStart: number | null | undefined }) {
  if (lineStart == null) return null;
  return (
    <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
      line {lineStart}
    </span>
  );
}

function StatusButton({
  current,
  value,
  label,
  onClick,
}: {
  current?: string | undefined;
  value: FindingStatus;
  label: string;
  onClick: () => void;
}) {
  const isActive = current === value;
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-[10px] rounded px-1.5 py-0.5 border transition-colors ${
        isActive
          ? "bg-[var(--color-bg-selected)] text-[var(--color-text-primary)] border-[var(--color-border-hover)]"
          : "border-[var(--color-border-default)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-hover)]"
      }`}
    >
      {label}
    </button>
  );
}
