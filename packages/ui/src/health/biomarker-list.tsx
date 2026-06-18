"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { InfoTip } from "../shared/info-tip";
import { biomarkerLabel, biomarkerInfo, CATEGORY_LABEL } from "./biomarker-glossary";
import { BiomarkerDetails, type BiomarkerDetailsRecord } from "./biomarker-details";
import { SEVERITY_CHIP, SEVERITY_LABEL, SEVERITY_ORDER, type Severity } from "./tokens";

export interface BiomarkerFinding {
  id: string;
  file_path: string;
  biomarker_type: string;
  severity: Severity;
  function_name: string | null;
  health_impact: number;
  reason: string;
  details?: BiomarkerDetailsRecord | null;
}

export interface BiomarkerListProps {
  findings: BiomarkerFinding[];
  /** When true, group by biomarker type with collapsible sections. */
  grouped?: boolean;
  /** Optional minimum severity filter. */
  minSeverity?: Severity;
  onSelect?: ((f: BiomarkerFinding) => void) | undefined;
  maxPerGroup?: number;
  /** Click-handler for the partner-file chip on hidden_coupling rows. */
  onPartnerSelect?: ((path: string) => void) | undefined;
  /** Optional anchor href for the partner-file chip. */
  onPartnerHref?: ((path: string) => string) | undefined;
}

export function BiomarkerList({
  findings,
  grouped = false,
  minSeverity,
  onSelect,
  maxPerGroup = 8,
  onPartnerSelect,
  onPartnerHref,
}: BiomarkerListProps) {
  const filtered = minSeverity
    ? findings.filter((f) => SEVERITY_ORDER[f.severity] >= SEVERITY_ORDER[minSeverity])
    : findings;

  if (filtered.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-secondary)]">
        No biomarker findings match the current filters.
      </div>
    );
  }

  if (!grouped) {
    return (
      <ul className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] divide-y divide-[var(--color-border-default)]">
        {filtered.map((f) => (
          <FindingRow
            key={f.id}
            finding={f}
            onSelect={onSelect}
            onPartnerSelect={onPartnerSelect}
            onPartnerHref={onPartnerHref}
          />
        ))}
      </ul>
    );
  }

  // Group by biomarker_type
  const groups = new Map<string, BiomarkerFinding[]>();
  for (const f of filtered) {
    if (!groups.has(f.biomarker_type)) groups.set(f.biomarker_type, []);
    groups.get(f.biomarker_type)!.push(f);
  }
  const sortedGroups = [...groups.entries()].sort(
    (a, b) => b[1].length - a[1].length,
  );

  return (
    <div className="space-y-2">
      {sortedGroups.map(([type, group]) => (
        <BiomarkerGroup
          key={type}
          type={type}
          findings={group}
          onSelect={onSelect}
          maxPerGroup={maxPerGroup}
          onPartnerSelect={onPartnerSelect}
          onPartnerHref={onPartnerHref}
        />
      ))}
    </div>
  );
}

function BiomarkerGroup({
  type,
  findings,
  onSelect,
  maxPerGroup,
  onPartnerSelect,
  onPartnerHref,
}: {
  type: string;
  findings: BiomarkerFinding[];
  onSelect?: ((f: BiomarkerFinding) => void) | undefined;
  maxPerGroup: number;
  onPartnerSelect?: ((path: string) => void) | undefined;
  onPartnerHref?: ((path: string) => string) | undefined;
}) {
  const [expanded, setExpanded] = useState(false);
  const info = biomarkerInfo(type);
  const sevCounts = { critical: 0, high: 0, medium: 0, low: 0 } as Record<Severity, number>;
  for (const f of findings) sevCounts[f.severity]++;
  const visible = expanded ? findings : findings.slice(0, maxPerGroup);
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-[var(--color-bg-elevated)] transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-[var(--color-text-tertiary)]" />
        ) : (
          <ChevronRight className="h-4 w-4 text-[var(--color-text-tertiary)]" />
        )}
        <span className="text-sm font-medium text-[var(--color-text-primary)]">
          {info.label}
        </span>
        {info.description ? (
          <InfoTip content={info.description} label={`About ${info.label}`} />
        ) : null}
        <span className="text-xs text-[var(--color-text-tertiary)]">
          {CATEGORY_LABEL[info.category]}
        </span>
        <span className="ml-auto inline-flex items-center gap-1.5 text-xs tabular-nums">
          {(Object.keys(sevCounts) as Severity[]).map((s) =>
            sevCounts[s] > 0 ? (
              <span
                key={s}
                className={`inline-flex items-center rounded px-1.5 py-0.5 font-semibold ${SEVERITY_CHIP[s]}`}
                title={SEVERITY_LABEL[s]}
              >
                {sevCounts[s]}
              </span>
            ) : null,
          )}
          <span className="ml-1 text-[var(--color-text-secondary)]">
            {findings.length}
          </span>
        </span>
      </button>
      <ul className="divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)]">
        {visible.map((f) => (
          <FindingRow
            key={f.id}
            finding={f}
            onSelect={onSelect}
            hideBiomarker
            onPartnerSelect={onPartnerSelect}
            onPartnerHref={onPartnerHref}
          />
        ))}
        {!expanded && findings.length > maxPerGroup ? (
          <li className="px-3 py-2 text-xs text-[var(--color-text-tertiary)]">
            + {findings.length - maxPerGroup} more — click header to expand
          </li>
        ) : null}
      </ul>
    </div>
  );
}

function FindingRow({
  finding,
  onSelect,
  hideBiomarker = false,
  onPartnerSelect,
  onPartnerHref,
}: {
  finding: BiomarkerFinding;
  onSelect?: ((f: BiomarkerFinding) => void) | undefined;
  hideBiomarker?: boolean;
  onPartnerSelect?: ((path: string) => void) | undefined;
  onPartnerHref?: ((path: string) => string) | undefined;
}) {
  const f = finding;
  const interactive = !!onSelect;
  return (
    <li
      className={`p-3 space-y-1 ${interactive ? "cursor-pointer hover:bg-[var(--color-bg-elevated)]" : ""}`}
      onClick={interactive ? () => onSelect!(f) : undefined}
    >
      <div className="flex items-center gap-2">
        <span
          className={`inline-block rounded px-2 py-0.5 text-[10px] uppercase font-semibold ${SEVERITY_CHIP[f.severity]}`}
        >
          {SEVERITY_LABEL[f.severity]}
        </span>
        {!hideBiomarker ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-text-primary)]">
            {biomarkerLabel(f.biomarker_type)}
            {biomarkerInfo(f.biomarker_type).description ? (
              <InfoTip
                content={biomarkerInfo(f.biomarker_type).description}
                label={`About ${biomarkerLabel(f.biomarker_type)}`}
              />
            ) : null}
          </span>
        ) : null}
        <span className="ml-auto text-xs tabular-nums text-[var(--color-error)]">
          −{f.health_impact.toFixed(2)}
        </span>
      </div>
      <p className="text-xs text-[var(--color-text-secondary)] truncate font-mono">
        {f.file_path}
        {f.function_name ? ` :: ${f.function_name}` : ""}
      </p>
      <p className="text-xs text-[var(--color-text-tertiary)] line-clamp-2">{f.reason}</p>
      <BiomarkerDetails
        biomarkerType={f.biomarker_type}
        details={f.details}
        onPartnerSelect={onPartnerSelect}
        onPartnerHref={onPartnerHref}
      />
    </li>
  );
}
