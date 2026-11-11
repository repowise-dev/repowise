"use client";

/**
 * The file drawer's findings: "What's wrong" (the top kinds of problem, in
 * plain words, each opening onto its evidence) and the collapsed "Other
 * issues" below it, grouped by the function they fire on. Triage sits behind
 * one menu per row.
 */

import { useId, useState } from "react";
import type { ReactNode } from "react";
import { ChevronRight, MoreHorizontal } from "lucide-react";
import { InfoTip } from "../../shared/info-tip";
import { CollapsibleSection } from "../../shared/collapsible-section";
import { Popover, PopoverContent, PopoverTrigger } from "../../ui/popover";
import { cn } from "../../lib/cn";
import {
  biomarkerInfo,
  biomarkerLabel,
  HISTORY_CHIP,
  HISTORY_EXPLAINER,
  HISTORY_LABEL,
  isHistoryBiomarker,
} from "../biomarker-glossary";
import { BiomarkerDetails } from "../biomarker-details";
import { FindingActions } from "../finding-actions";
import { plainIssue } from "../plain-language";
import { SeverityMark } from "../severity-mark";
import { VerificationTag } from "../verification-tag";
import { LowerPriorityTag } from "../lower-priority-tag";
import { PreviewBadge } from "../preview-badge";
import { ImpactFigure } from "../impact-figure";
import type { GatedByLanguage } from "@repowise-dev/types/health";
import type { HealthDrawerFinding } from "../health-file-drawer";
import {
  findingLocation,
  statusOf,
  type FindingSection,
  type StatusOverrides,
  type TopIssue,
} from "./model";
import { MICRO_LABEL, POOLED_FILE_KEY, QUIET_LINK, STATUS_LABEL, TRIAGE_STATUSES } from "./shared";
import { formatNumber } from "../../lib/format";

/** What every row needs from the drawer: links, triage, and its opened detail. */
export interface RowContext {
  fileViewHrefFor: ((lineStart: number) => string) | undefined;
  savingId: string | null;
  onStatusChange: ((id: string, status: string) => Promise<void>) | undefined;
  overrides: StatusOverrides;
  detail: (f: HealthDrawerFinding) => ReactNode;
}

/** The evidence and suggested fix a row opens onto. */
export function FindingDetail({
  finding: f,
  suggestion,
  onPartnerSelect,
  onPartnerHref,
}: {
  finding: HealthDrawerFinding;
  suggestion: string | undefined;
  onPartnerSelect?: ((path: string) => void) | undefined;
  onPartnerHref?: ((path: string) => string) | undefined;
}) {
  return (
    <>
      <BiomarkerDetails
        biomarkerType={f.biomarker_type}
        details={f.details}
        onPartnerSelect={onPartnerSelect}
        onPartnerHref={onPartnerHref}
      />
      {suggestion ? (
        <p className="text-xs text-[var(--color-text-secondary)]">
          <span className="font-medium text-[var(--color-text-primary)]">Suggested fix: </span>
          {suggestion}
        </p>
      ) : null}
      <FindingActions details={f.details} />
    </>
  );
}

export function WhatsWrong({ issues, ctx }: { issues: TopIssue[]; ctx: RowContext }) {
  if (issues.length === 0) return null;
  return (
    <section className="flex flex-col gap-1" aria-label="What's wrong">
      <h3 className={MICRO_LABEL}>What&rsquo;s wrong</h3>
      <ul className="flex flex-col">
        {issues.map(({ lead, more }) => (
          <IssueRow key={lead.id} finding={lead} more={more} ctx={ctx} />
        ))}
      </ul>
    </section>
  );
}

/**
 * One "What's wrong" row: a plain title, where it is, a small impact figure and
 * why it matters. Pressing the row opens the evidence and the suggested fix
 * inline; triage stays behind the row's menu.
 */
function IssueRow({
  finding: f,
  more,
  ctx,
}: {
  finding: HealthDrawerFinding;
  /** Other places the same kind of problem fires, listed under Other issues. */
  more: number;
  ctx: RowContext;
}) {
  const [open, setOpen] = useState(false);
  const plain = plainIssue(f.biomarker_type);
  const location = findingLocation(f, ctx.fileViewHrefFor);
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
          className="flex min-w-0 flex-1 flex-col gap-0.5 rounded-sm py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
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
          <RowActions
            finding={f}
            label={location ? `${plain.title} at ${location.label}` : plain.title}
            ctx={ctx}
          />
        </span>
      </div>
      {open ? (
        <div id={bodyId} className="flex flex-col gap-2 pb-3 pl-[22px]">
          <IssueEvidence finding={f} href={location?.href} label={location?.label} />
          {ctx.detail(f)}
        </div>
      ) : null}
    </li>
  );
}

/** The detector's own name and reason, its exception tags, and the line link. */
function IssueEvidence({
  finding: f,
  href,
  label,
}: {
  finding: HealthDrawerFinding;
  href: string | undefined;
  label: string | undefined;
}) {
  return (
    <>
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
      {href ? (
        <a href={href} className={cn(QUIET_LINK, "font-mono")}>
          Go to {label}
        </a>
      ) : null}
    </>
  );
}

/** A row's triage status, its save in flight, and the menu that changes it. */
function RowActions({
  finding: f,
  label,
  ctx,
}: {
  finding: HealthDrawerFinding;
  label: string;
  ctx: RowContext;
}) {
  const status = statusOf(f, ctx.overrides);
  const saving = ctx.savingId === f.id;
  const onStatusChange = ctx.onStatusChange;
  return (
    <>
      {status !== "open" ? (
        <span className="rounded border border-[var(--color-border-default)] px-1.5 text-[10px] text-[var(--color-text-secondary)]">
          {STATUS_LABEL[status] ?? status}
        </span>
      ) : null}
      {saving ? <span className="text-[10px] text-[var(--color-text-tertiary)]">Saving…</span> : null}
      {onStatusChange ? (
        <TriageMenu
          label={label}
          status={status}
          disabled={saving}
          onSelect={(next) => void onStatusChange(f.id, next).catch(() => {})}
        />
      ) : null}
    </>
  );
}

export function OtherIssues({
  rest,
  sections,
  leading,
  heldBack,
  heldBackTotal,
  ctx,
}: {
  rest: HealthDrawerFinding[];
  sections: FindingSection[];
  /** How many rows lead above, so the title says what this list leaves out. */
  leading: number;
  heldBack: GatedByLanguage | null | undefined;
  heldBackTotal: number;
  ctx: RowContext;
}) {
  if (rest.length === 0 && heldBackTotal === 0) return null;
  const count = formatNumber(rest.length);
  return (
    <CollapsibleSection
      title={leading > 0 ? `Other issues (${count})` : `All issues (${count})`}
      hint={otherIssuesHint(leading, sections.length)}
    >
      {heldBackTotal > 0 ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          <span className="tabular-nums">{formatNumber(heldBackTotal)}</span> more{" "}
          {heldBackTotal === 1 ? "finding is" : "findings are"} held back:{" "}
          {Object.values(heldBack ?? {})
            .map((g) => g.reason)
            .join(" ")}
        </p>
      ) : null}
      <div className="flex flex-col">
        {sections.map((s) => (
          <CollapsibleSection
            key={s.isFile ? `file:${s.key}` : `fn:${s.key}`}
            dense
            // Single-issue groups have nothing to collapse; the rest open as
            // compact headers.
            defaultOpen={s.group.length === 1}
            title={<GroupTitle section={s} />}
            hint={<GroupHint section={s} />}
          >
            <ul className="flex flex-col pl-5">
              {s.group.map((f) => (
                <FindingRow key={f.id} finding={f} ctx={ctx} />
              ))}
            </ul>
          </CollapsibleSection>
        ))}
      </div>
    </CollapsibleSection>
  );
}

function otherIssuesHint(leading: number, groups: number): string | undefined {
  if (leading > 0) return `not repeating the ${leading} above`;
  return groups > 1 ? `in ${groups} places` : undefined;
}

/** A group's count and total, and its cap when the group is the whole category. */
function GroupHint({ section: s }: { section: FindingSection }) {
  return (
    <>
      <span>
        {s.group.length} {s.group.length === 1 ? "issue" : "issues"}
      </span>
      <ImpactFigure impact={s.total} tone="muted" />
      {/* Why 34 issues cost half a point. Without this the count reads as the
          severity and the held total looks like a bug. The explanation rides
          in the accessible name, because a bare `title` never reaches it. */}
      {s.atCap !== null ? (
        <span
          className="rounded-sm border border-[var(--color-border-default)] px-1 text-[10px] text-[var(--color-text-tertiary)]"
          aria-label={`at limit: held at its ${s.atCap.toFixed(2)}-point ceiling, so more issues of this kind take nothing further off`}
        >
          at limit
        </span>
      ) : null}
    </>
  );
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

/**
 * One finding in "Other issues", as a hairline row with whatever the opened
 * detail carries (evidence, suggestion).
 *
 * Chips mark exceptions only: a provisional type, a lower-priority reason, a
 * preview cell, a history marker, a triaged status. Every row carrying its
 * category and pillar communicated nothing.
 */
function FindingRow({ finding: f, ctx }: { finding: HealthDrawerFinding; ctx: RowContext }) {
  const info = biomarkerInfo(f.biomarker_type);
  const label = biomarkerLabel(f.biomarker_type);
  const location = findingLocation(f, ctx.fileViewHrefFor);
  return (
    <li className="flex flex-col gap-1 border-t border-[var(--color-border-default)] py-2.5 first:border-t-0">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <SeverityMark severity={f.severity} />
        <span className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--color-text-primary)]">
          {label}
          {info.description ? <InfoTip content={info.description} label={`About ${label}`} /> : null}
        </span>
        <VerificationTag verification={f.verification} />
        <LowerPriorityTag reason={f.lower_priority} />
        <PreviewBadge note={f.preview} />
        {isHistoryBiomarker(f.biomarker_type) ? <WatchChip /> : null}
        <LocationText location={location} />
        <span className="ml-auto inline-flex shrink-0 items-center gap-1">
          <ImpactFigure impact={f.health_impact} tone="muted" className="text-xs" />
          <RowActions
            finding={f}
            label={location ? `${label} at ${location.label}` : label}
            ctx={ctx}
          />
        </span>
      </div>
      <p className="text-xs text-[var(--color-text-secondary)]">{f.reason}</p>
      {ctx.detail(f)}
    </li>
  );
}

/** Scored, but nothing in this file will clear it. */
function WatchChip() {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-px text-[10px] font-medium ${HISTORY_CHIP}`}
    >
      {HISTORY_LABEL}
      <InfoTip content={HISTORY_EXPLAINER} label="Why this is a watch item" />
    </span>
  );
}

function LocationText({ location }: { location: ReturnType<typeof findingLocation> }) {
  if (!location) return null;
  if (location.href) {
    return (
      <a href={location.href} className={cn(QUIET_LINK, "shrink-0 font-mono font-normal")}>
        {location.label}
      </a>
    );
  }
  return (
    <span className="shrink-0 font-mono text-xs text-[var(--color-text-tertiary)]">
      {location.label}
    </span>
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
        className="rounded-md p-1 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-selected)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] disabled:opacity-50"
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
                  "block w-full rounded px-2 py-1.5 text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]",
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
