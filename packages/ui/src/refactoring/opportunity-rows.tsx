"use client";

/**
 * The opportunity list, as hairline rows. One row is one file's refactoring.
 *
 * This replaced a grid of `RefactoringPlanCard`s, and then replaced the plan
 * rows that replaced them. Each card carried a type-coloured rail, a tinted
 * type chip, a bordered metric footer and a second bordered strip holding its
 * own AI-prompt button - four borders and two click targets per plan, sixty at
 * a time - and forced three truncations each, which is a layout decision
 * reported to the reader as missing content.
 *
 * The unit change is the bigger one. A row used to be a detector output, so a
 * file with fifteen of them filled the screen fifteen times and the reader had
 * to reassemble "this file needs work" themselves. A row is now the composed
 * opportunity: the file, what it leads with, how many steps, and how many of
 * those are mechanical.
 *
 * Marks stay conditional rather than universal, per "mark only what needs
 * attention": confidence renders only when it is not high, recovered health
 * only when there is any, and lifecycle only once a person has moved it off
 * `open`.
 */

import * as React from "react";

import { formatNumber } from "../lib/format";
import { typeMeta } from "./meta";
import {
  STATUS_LABEL,
  TRIAGE_STATUSES,
  addressesPrimaryLabel,
  addressesPrimaryShort,
  opportunityLede,
  stepSummary,
} from "./opportunity";
import type {
  OpportunityStatus,
  RefactoringOpportunity,
} from "@repowise-dev/types/refactoring";

export interface OpportunityRowsProps {
  opportunities: RefactoringOpportunity[];
  onOpen?: ((opportunity: RefactoringOpportunity) => void) | undefined;
  /** The overflow verb: hand this opportunity to a coding agent. */
  onAiPrompt?: ((opportunity: RefactoringOpportunity) => void) | undefined;
  /** Record a triage decision. Controls hide entirely when absent. */
  onStatusChange?:
    | ((
        opportunity: RefactoringOpportunity,
        status: OpportunityStatus,
      ) => Promise<void> | void)
    | undefined;
  /** Link to the file, used by the overflow menu. */
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  /** Lit from the map above, when a row is also plotted. */
  highlightedId?: string | null | undefined;
  onHighlight?: ((id: string | null) => void) | undefined;
}

const EFFORT_WORD: Record<string, string> = {
  S: "Small",
  M: "Medium",
  L: "Large",
  XL: "Extra large",
};

/**
 * One grid, named once.
 *
 * The header and the rows have to share a template or they drift the first time
 * a column is resized, and a header that does not line up with its column is
 * worse than no header. The file column is the flexible one because it is the
 * only cell whose content has no natural width.
 */
const GRID =
  // Below `lg` the first track is fixed rather than `1fr` against an `auto`
  // sibling: each row is its own grid, so an `auto` second track sized to its
  // own row and left the labels on a ragged edge, wrapping the type mid-phrase
  // on the wider rows only.
  "grid grid-cols-[8.5rem_minmax(0,1fr)] gap-x-5 gap-y-2 px-3 " +
  "lg:grid-cols-[116px_minmax(0,1fr)_150px_104px_96px_32px]";

export function OpportunityRows({
  opportunities,
  onOpen,
  onAiPrompt,
  onStatusChange,
  fileHref,
  highlightedId,
  onHighlight,
}: OpportunityRowsProps) {
  return (
    <div className="flex flex-col">
      {/* Hidden below `lg`, where the row stacks and the labels would outnumber
          the values they label. */}
      <div
        aria-hidden
        className={`${GRID} hidden border-b border-[var(--color-border-default)] pb-2 pt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] lg:grid`}
      >
        <span>Type</span>
        <span>File</span>
        <span>Work</span>
        <span>Health</span>
        <span>Status</span>
        <span className="sr-only">Actions</span>
      </div>
      {opportunities.map((opportunity) => (
        <OpportunityRow
          key={opportunity.opportunity_id}
          opportunity={opportunity}
          onOpen={onOpen}
          onAiPrompt={onAiPrompt}
          onStatusChange={onStatusChange}
          fileHref={fileHref}
          onHighlight={onHighlight}
          lit={opportunity.opportunity_id === highlightedId}
        />
      ))}
    </div>
  );
}

function OpportunityRow({
  opportunity,
  onOpen,
  onAiPrompt,
  onStatusChange,
  fileHref,
  onHighlight,
  lit,
}: {
  opportunity: RefactoringOpportunity;
  onOpen?: ((opportunity: RefactoringOpportunity) => void) | undefined;
  onAiPrompt?: ((opportunity: RefactoringOpportunity) => void) | undefined;
  onStatusChange?:
    | ((
        opportunity: RefactoringOpportunity,
        status: OpportunityStatus,
      ) => Promise<void> | void)
    | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  onHighlight?: ((id: string | null) => void) | undefined;
  lit: boolean;
}) {
  const meta = typeMeta(opportunity.lead_refactoring_type || "");
  const effort = EFFORT_WORD[opportunity.effort_bucket] ?? opportunity.effort_bucket;
  const href = fileHref?.(opportunity.file_path);
  const gain = opportunity.recoverable_health;
  const name = opportunity.file_path.split("/").pop() ?? opportunity.file_path;

  // Optimistic, with the server's answer as the authority. A triage click that
  // waits for a round trip reads as a dead control on a list this long.
  const [pending, setPending] = React.useState<OpportunityStatus | null>(null);
  const [failed, setFailed] = React.useState(false);
  const status = pending ?? opportunity.status;

  const setStatus = React.useCallback(
    async (next: OpportunityStatus) => {
      if (!onStatusChange) return;
      setPending(next);
      setFailed(false);
      try {
        await onStatusChange(opportunity, next);
      } catch {
        // Roll back to whatever the row was handed, and say so rather than
        // leaving a state the server never accepted looking committed.
        setPending(null);
        setFailed(true);
      }
    },
    [onStatusChange, opportunity],
  );

  React.useEffect(() => {
    // The refetched row is the truth; drop the optimistic value once it lands.
    setPending(null);
  }, [opportunity.status]);

  return (
    <div
      data-refactoring-opportunity={opportunity.opportunity_id}
      onMouseEnter={() => onHighlight?.(opportunity.opportunity_id)}
      onMouseLeave={() => onHighlight?.(null)}
      className={`${GRID} items-start border-t border-[var(--color-border-default)] py-3 ${
        lit ? "bg-[var(--color-accent-muted)]" : "hover:bg-[var(--color-bg-elevated)]"
      }`}
    >
      <div className="order-2 text-xs text-[var(--color-text-secondary)] lg:order-none lg:pt-px">
        {meta.label}
      </div>

      <button
        type="button"
        onClick={onOpen ? () => onOpen(opportunity) : undefined}
        disabled={!onOpen}
        onFocus={() => onHighlight?.(opportunity.opportunity_id)}
        onBlur={() => onHighlight?.(null)}
        className="group order-1 col-span-2 min-w-0 text-left lg:order-none lg:col-span-1"
      >
        <span className="block break-words font-mono text-[13.5px] font-medium text-[var(--color-text-primary)] group-hover:text-[var(--color-accent-primary)]">
          {name}
        </span>
        {/* Full path, no ellipsis: a truncated title reports a layout decision
            to the reader as missing content. */}
        <span className="mt-0.5 block break-all font-mono text-[11.5px] text-[var(--color-text-tertiary)]">
          {opportunity.file_path}
        </span>
        <span className="mt-1 block text-[12px] text-[var(--color-text-secondary)]">
          {opportunityLede(opportunity)}
        </span>
      </button>

      <div className="order-3 text-[12.5px] text-[var(--color-text-secondary)] lg:order-none lg:pt-px">
        {stepSummary(opportunity)}
        <br />
        <span className="text-[11.5px] text-[var(--color-text-tertiary)]">
          {effort} effort
          {opportunity.affected_files_total > 1
            ? `, ${formatNumber(opportunity.affected_files_total)} files`
            : ""}
        </span>
        {opportunity.confidence !== "high" ? (
          <span className="ml-2 inline-flex items-center gap-1.5 text-[11.5px] text-[var(--color-caution)]">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
            {opportunity.confidence} confidence
          </span>
        ) : null}
      </div>

      <div className="order-4 space-y-1 text-[12.5px] tabular-nums lg:order-none lg:pt-px">
        {gain > 0 ? (
          <span className="block font-medium text-[var(--color-success)]">
            +{gain.toFixed(1)} health
          </span>
        ) : (
          <span className="block text-[var(--color-text-tertiary)]">no score change</span>
        )}
        {/* Tri-state, and the unknown case says so. Reading "no" onto "we could
            not tell" would turn a missing input into an accusation. */}
        <span
          title={addressesPrimaryLabel(opportunity.addresses_primary_problem)}
          className={
            opportunity.addresses_primary_problem === true
              ? "block text-[11.5px] text-[var(--color-text-secondary)]"
              : "block text-[11.5px] text-[var(--color-text-tertiary)]"
          }
        >
          {addressesPrimaryShort(opportunity.addresses_primary_problem)}
        </span>
      </div>

      {/* Its own column. Triage was a line that appeared inside the health cell
          only once a row left `open`, so the list had no column a reader could
          scan down to see what they had already dealt with - and the row's
          height changed when they marked one. */}
      <div className="order-5 text-[12.5px] lg:order-none lg:pt-px">
        <span
          className={
            status === "open"
              ? "text-[var(--color-text-tertiary)]"
              : "font-medium text-[var(--color-accent-primary)]"
          }
        >
          {STATUS_LABEL[status]}
        </span>
        {/* Rendered whether or not it has anything to say: several screen
            readers announce a text change inside a live region but not the
            arrival of the region itself. */}
        <span
          role="status"
          className={
            failed ? "mt-0.5 block text-[11.5px] text-[var(--color-error)]" : "sr-only"
          }
        >
          {failed ? "Could not save" : ""}
        </span>
      </div>

      <div className="order-6 justify-self-end lg:order-none lg:-mt-1">
        <RowOverflow
          opportunity={opportunity}
          status={status}
          onAiPrompt={onAiPrompt}
          onStatusChange={onStatusChange ? setStatus : undefined}
          href={href}
        />
      </div>
    </div>
  );
}

/**
 * The row's second-order verbs.
 *
 * Opening the opportunity is the row itself. Everything else lives here so a
 * 580-row list is not 580 clusters of controls the reader has to parse before
 * they can read a row - and so each verb can have its real name rather than an
 * abbreviation that only expands for someone who already knows the model.
 */
function RowOverflow({
  opportunity,
  status,
  onAiPrompt,
  onStatusChange,
  href,
}: {
  opportunity: RefactoringOpportunity;
  status: OpportunityStatus;
  onAiPrompt?: ((opportunity: RefactoringOpportunity) => void) | undefined;
  onStatusChange?: ((status: OpportunityStatus) => void) | undefined;
  href?: string | undefined;
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement | null>(null);
  const triggerRef = React.useRef<HTMLButtonElement | null>(null);

  // Closing unmounts the menu, and if the focused item was inside it the
  // browser drops focus to <body>. On a list this long that ejects a keyboard
  // reader to the top of the document on every triage action.
  const close = React.useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }, []);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      // A click elsewhere is the reader moving on; taking focus back would
      // fight them for it.
      if (ref.current && !ref.current.contains(e.target as Node)) close(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  if (!onAiPrompt && !href && !onStatusChange) return null;

  return (
    <div className="relative" ref={ref}>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`More actions for ${opportunity.file_path}`}
        onClick={() => setOpen((v) => !v)}
        className="rounded-md px-1.5 py-1 leading-none text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-inset)] hover:text-[var(--color-text-primary)]"
      >
        <span aria-hidden>&#183;&#183;&#183;</span>
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-full z-20 mt-1 w-60 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] py-1 shadow-md"
        >
          {onAiPrompt ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                close();
                onAiPrompt(opportunity);
              }}
              className="block w-full px-3 py-2 text-left text-[13px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
            >
              Copy prompt for an agent
            </button>
          ) : null}
          {href ? (
            <a
              href={href}
              role="menuitem"
              className="block px-3 py-2 text-[13px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
            >
              Open file
            </a>
          ) : null}
          {onStatusChange ? (
            <div
              role="group"
              aria-label="Triage this opportunity"
              className="mt-1 border-t border-[var(--color-border-default)] pt-1"
            >
              <p className="px-3 pb-1 pt-1 text-[11px] uppercase tracking-[0.08em] text-[var(--color-text-tertiary)]">
                Triage
              </p>
              {TRIAGE_STATUSES.map((option) => {
                const current = option.value === status;
                return (
                  <button
                    key={option.value}
                    type="button"
                    role="menuitemradio"
                    aria-checked={current}
                    disabled={current}
                    onClick={() => {
                      close();
                      onStatusChange(option.value);
                    }}
                    className={`block w-full px-3 py-2 text-left text-[13px] ${
                      current
                        ? "font-medium text-[var(--color-accent-primary)]"
                        : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
