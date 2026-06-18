"use client";

import { useState } from "react";
import { ArrowUpRight, ChevronDown, ChevronRight, Sparkles } from "lucide-react";
import { InfoTip } from "../shared/info-tip";
import { biomarkerInfo, biomarkerLabel } from "./biomarker-glossary";
import type { BiomarkerDetailsRecord } from "./biomarker-details";
import { SEVERITY_CHIP, SEVERITY_LABEL, type Severity } from "./tokens";

export type EffortBucket = "S" | "M" | "L" | "XL";

export interface RefactoringTargetFinding {
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
}

export interface RefactoringTarget {
  file_path: string;
  score: number;
  nloc: number;
  module?: string | null;
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
  biomarkers: string[];
  effort_bucket: EffortBucket;
  impact_per_effort: number;
  all_findings?: RefactoringTargetFinding[];
}

export type FindingStatus = "open" | "acknowledged" | "resolved" | "false_positive";

export interface RefactoringCardProps {
  target: RefactoringTarget;
  onSelect?: ((target: RefactoringTarget) => void) | undefined;
  onStatusChange?: ((findingId: string, status: FindingStatus) => void) | undefined;
  onGeneratePrompt?: ((target: RefactoringTarget) => void) | undefined;
  expandable?: boolean;
  /** Flash-highlight the card (e.g. after a quadrant dot click scrolled to it). */
  highlighted?: boolean;
}

const effortLabel: Record<EffortBucket, string> = {
  S: "Small",
  M: "Medium",
  L: "Large",
  XL: "Extra large",
};

const effortColor: Record<EffortBucket, string> = {
  S: "bg-[var(--color-success)]/15 text-[var(--color-success)]",
  M: "bg-[var(--color-caution)]/15 text-[var(--color-caution)]",
  L: "bg-[var(--color-warning)]/15 text-[var(--color-warning)]",
  XL: "bg-[var(--color-error)]/15 text-[var(--color-error)]",
};

export function RefactoringCard({
  target,
  onSelect,
  onStatusChange,
  onGeneratePrompt,
  expandable = true,
  highlighted = false,
}: RefactoringCardProps) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      data-refactoring-card={target.file_path}
      className={`rounded-lg border bg-[var(--color-bg-surface)] overflow-hidden transition-colors ${
        highlighted
          ? "border-[var(--color-accent-primary)] ring-1 ring-[var(--color-accent-primary)]/40"
          : "border-[var(--color-border-default)]"
      }`}
    >
      <div className="p-4 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`inline-block rounded px-2 py-0.5 text-[10px] uppercase font-semibold ${SEVERITY_CHIP[target.primary_severity]}`}
          >
            {SEVERITY_LABEL[target.primary_severity]}
          </span>
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--color-text-primary)]">
            {biomarkerLabel(target.primary_biomarker)}
            {biomarkerInfo(target.primary_biomarker).description ? (
              <InfoTip
                content={biomarkerInfo(target.primary_biomarker).description}
                label={`About ${biomarkerLabel(target.primary_biomarker)}`}
              />
            ) : null}
          </span>
          {target.module ? (
            <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)] rounded px-1.5 py-0.5 border border-[var(--color-border-default)]">
              {target.module}
            </span>
          ) : null}
          <span
            className={`inline-block rounded px-1.5 py-0.5 text-[10px] uppercase font-semibold ${effortColor[target.effort_bucket]}`}
            title={`Effort: ${effortLabel[target.effort_bucket]} (NLOC ${target.nloc})`}
          >
            {target.effort_bucket}
          </span>
          <span className="ml-auto text-xs tabular-nums text-[var(--color-error)]" title="Total health impact across this file's findings">
            −{target.total_impact.toFixed(2)}
          </span>
        </div>
        <button
          type="button"
          onClick={onSelect ? () => onSelect(target) : undefined}
          className="group/file flex w-full items-center gap-1.5 text-left rounded-md -mx-1 px-1 py-0.5 hover:bg-[var(--color-bg-elevated)] disabled:cursor-default disabled:hover:bg-transparent"
          disabled={!onSelect}
          title={onSelect ? "Open file health drawer" : undefined}
        >
          <p className="min-w-0 flex-1 text-sm font-mono text-[var(--color-text-primary)] truncate group-hover/file:text-[var(--color-accent-primary)]">
            {target.file_path}
            {target.primary_function ? (
              <span className="text-[var(--color-text-secondary)]">
                {" :: "}
                {target.primary_function}
              </span>
            ) : null}
          </p>
          {onSelect ? (
            <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] group-hover/file:text-[var(--color-accent-primary)] transition-transform group-hover/file:-translate-y-px group-hover/file:translate-x-px" />
          ) : null}
        </button>
        <p className="text-xs text-[var(--color-text-secondary)] line-clamp-2">{target.primary_reason}</p>
        {target.primary_suggestion ? (
          <p className="text-xs text-[var(--color-text-tertiary)] italic line-clamp-3">
            {target.primary_suggestion}
          </p>
        ) : null}
        <div className="flex items-center gap-3 pt-1 text-xs text-[var(--color-text-tertiary)] flex-wrap">
          <span>Score {target.score.toFixed(1)}/10</span>
          <span>· {target.nloc} NLOC</span>
          <span>· {effortLabel[target.effort_bucket]} effort</span>
          <span>· {target.finding_count} findings</span>
          <span className="ml-auto tabular-nums">leverage {target.impact_per_effort.toFixed(2)}</span>
        </div>
        {onGeneratePrompt ? (
          <div className="pt-2">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onGeneratePrompt(target);
              }}
              className="group/ai inline-flex items-center gap-1.5 rounded-md border border-[var(--color-success)]/40 bg-[var(--color-success)]/10 px-2.5 py-1 text-xs font-semibold text-[var(--color-success)] hover:bg-[var(--color-success)]/20 hover:border-[var(--color-success)]/60 transition-colors"
              title="Generate a ready-to-paste prompt for an AI coding agent"
            >
              <Sparkles className="h-3.5 w-3.5 transition-transform group-hover/ai:rotate-12" />
              AI fix prompt
            </button>
          </div>
        ) : null}
      </div>
      {expandable && target.all_findings && target.all_findings.length > 0 ? (
        <>
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="flex w-full items-center gap-2 px-4 py-2 text-xs text-[var(--color-text-secondary)] border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]"
          >
            {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            {expanded ? "Hide" : "Show"} all {target.all_findings.length} findings
          </button>
          {expanded ? (
            <ul className="divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)]">
              {target.all_findings.map((f) => (
                <li key={f.id} className="px-4 py-2 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className={`inline-block rounded px-1.5 py-px text-[10px] uppercase font-semibold ${SEVERITY_CHIP[f.severity]}`}
                    >
                      {SEVERITY_LABEL[f.severity]}
                    </span>
                    <span className="text-xs font-medium text-[var(--color-text-primary)]">
                      {biomarkerLabel(f.biomarker_type)}
                    </span>
                    {f.function_name ? (
                      <span className="text-xs font-mono text-[var(--color-text-tertiary)]">{f.function_name}</span>
                    ) : null}
                    <span className="ml-auto text-xs tabular-nums text-[var(--color-error)]">
                      −{f.health_impact.toFixed(2)}
                    </span>
                  </div>
                  <p className="text-xs text-[var(--color-text-tertiary)] line-clamp-2">{f.reason}</p>
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
          ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)] border-[var(--color-accent-primary)]/50"
          : "border-[var(--color-border-default)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-strong)]"
      }`}
    >
      {label}
    </button>
  );
}
