"use client";

import { memo, useState } from "react";
import { Copy, FileCode, MoreHorizontal } from "lucide-react";
import { toast } from "sonner";
import type { PerformanceOpportunity } from "@repowise-dev/types/health";

import { Button } from "../../ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../../ui/popover";
import { FilePath } from "../../shared/file-path";
import { InfoTip } from "../../shared/info-tip";
import { CLICKABLE_ROW_CLS, clickableRowProps } from "../../shared/responsive-table";
import { RowPromptAction } from "../row-prompt-action";
import { MICRO } from "../tokens";
import { ActionabilityMark } from "./actionability-mark";
import type { PerformanceViewAdapter } from "./adapter";
import {
  affectedSummary,
  agentHandoffCall,
  causeStandingText,
  effortLabel,
  FIX_SAFETY_LABEL,
  fixStrategyLabel,
  opportunityCause,
  opportunitySubject,
  opportunityTitle,
  planPresentation,
} from "./presentation";
import { OpportunityTags } from "./tags";

/**
 * Action-first rows: what to change, what it buys and what it costs, then the
 * verbs. The agent prompt is the primary one, because people fix these through
 * their agents; inspecting, the plan and the file are secondary.
 */

export interface RowCallbacks {
  onInspect: (opportunity: PerformanceOpportunity) => void;
  onPrompt: (opportunity: PerformanceOpportunity) => void;
  /** Warm the detail the drawer and the prompt read, on hover or focus. */
  onPrefetch: (opportunityId: string) => void;
}

export function OpportunityList({
  items,
  adapter,
  selectedId,
  callbacks,
  label,
}: {
  items: PerformanceOpportunity[];
  adapter: PerformanceViewAdapter;
  selectedId: string | null;
  callbacks: RowCallbacks;
  label: string;
}) {
  return (
    <ul
      aria-label={label}
      className="divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)]"
    >
      {items.map((opportunity) => (
        <OpportunityRow
          key={opportunity.opportunity_id}
          opportunity={opportunity}
          adapter={adapter}
          selected={opportunity.opportunity_id === selectedId}
          onInspect={callbacks.onInspect}
          onPrompt={callbacks.onPrompt}
          onPrefetch={callbacks.onPrefetch}
        />
      ))}
    </ul>
  );
}

/** The change to make, and what it buys, in secondary ink. */
function ActionLine({ opportunity }: { opportunity: PerformanceOpportunity }) {
  const { fix, gain_text: gain } = opportunity;
  if (!fix) {
    return (
      <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
        No supported fix yet: read the evidence before changing anything.
      </p>
    );
  }
  return (
    <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
      <span className={`${MICRO} mr-1.5`}>Action</span>{" "}
      <span>{fixStrategyLabel(fix.strategy)}</span>
      {gain ? <span>, saves {gain}</span> : null}
    </p>
  );
}

/**
 * The deciding facts on one quiet mono line: effort, safety and reach, each
 * only when known. Why it sits in its state, the plan's standing and how well
 * the path resolved inform a reader but do not decide the row, so they sit
 * behind the tip.
 */
function MetaLine({ opportunity }: { opportunity: PerformanceOpportunity }) {
  const effort = effortLabel(opportunity);
  const { fix } = opportunity;
  const parts: React.ReactNode[] = [];
  if (effort) parts.push(<span key="effort">{effort} effort</span>);
  if (fix) {
    parts.push(
      <span key="safety" className="inline-flex items-center gap-1">
        {fix.safety === "proven" ? (
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />
        ) : null}
        {FIX_SAFETY_LABEL[fix.safety]}
      </span>,
    );
  }
  parts.push(<span key="reach">{affectedSummary(opportunity)}</span>);
  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
      {parts.flatMap((part, index) =>
        index === 0 ? [part] : [<span key={`sep-${index}`} aria-hidden>·</span>, part],
      )}
      <InfoTip
        label="Why this cause sits here"
        content={causeStandingText(opportunity)}
      />
    </p>
  );
}

/** A quiet link: secondary ink at rest, the accent only on hover and focus. */
export const QUIET_LINK_CLS =
  "rounded text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-accent-primary)] hover:underline focus-visible:text-[var(--color-accent-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

/**
 * Memoized because a page is up to a hundred of these and the surrounding
 * filter state changes far more often than any single row does. It takes the
 * callbacks rather than closures over one opportunity so the props compare.
 */
const OpportunityRow = memo(function OpportunityRow({
  opportunity,
  adapter,
  selected,
  onInspect,
  onPrompt,
  onPrefetch,
}: {
  opportunity: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
  selected: boolean;
} & RowCallbacks) {
  const title = opportunityTitle(opportunity);
  const plan = planPresentation(opportunity);
  const planHref =
    plan.actionable && opportunity.plan_id
      ? adapter.refactoringPlanHref?.(opportunity.plan_id, opportunity.opportunity_id)
      : undefined;
  const prefetch = () => onPrefetch(opportunity.opportunity_id);

  return (
    <li
      data-performance-opportunity={opportunity.opportunity_id}
      aria-current={selected ? "true" : undefined}
      data-selected={selected || undefined}
      aria-label={`Inspect ${title}`}
      className={`${CLICKABLE_ROW_CLS} group/row flex flex-col gap-3 px-1 py-4 sm:flex-row sm:items-start sm:gap-4 ${
        selected ? "bg-[var(--color-bg-elevated)]" : "hover:bg-[var(--color-bg-elevated)]"
      }`}
      onMouseEnter={prefetch}
      onFocus={prefetch}
      {...clickableRowProps(() => onInspect(opportunity))}
    >
      <div className="flex min-w-0 flex-1 gap-3">
        <span className="mt-0.5 w-7 shrink-0 text-right font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {(opportunity.rank_position + 1).toLocaleString()}
        </span>
        <div className="min-w-0 flex-1">
          <ActionabilityMark state={opportunity.actionability_state} />
          <p className="mt-1 text-sm font-medium leading-snug text-[var(--color-text-primary)]">
            {opportunityCause(opportunity)} in{" "}
            <code className="font-mono text-[0.9em] [overflow-wrap:anywhere]">
              {opportunitySubject(opportunity)}
            </code>
          </p>
          {/* The path once, name first, the package prefix folded. */}
          <p className="mt-0.5 flex min-w-0 font-mono text-xs text-[var(--color-text-secondary)]">
            <FilePath path={opportunity.file_path} fold className="text-xs" />
          </p>
          <ActionLine opportunity={opportunity} />
          <MetaLine opportunity={opportunity} />
          <OpportunityTags opportunity={opportunity} />
          {planHref ? (
            <p className="mt-2 text-xs">
              <a
                href={planHref}
                onClick={(event) => event.stopPropagation()}
                className={QUIET_LINK_CLS}
              >
                Open plan
              </a>
            </p>
          ) : null}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1 pl-10 sm:pl-0">
        {/* The shared quiet row action: shown on hover, focus and the
            selected row, always in the tab order. The drawer holds the
            filled prompt. */}
        <RowPromptAction onClick={() => onPrompt(opportunity)} subject={title} />
        <RowOverflow
          opportunity={opportunity}
          title={title}
          onOpenFile={() => adapter.navigate(adapter.fileHref(opportunity.file_path))}
        />
      </div>
    </li>
  );
});

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied`);
  } catch {
    toast.error(`Could not copy the ${what.toLowerCase()}`);
  }
}

/** The row's other verbs: open the file, and the ids an agent or a script
 *  resolves the cause by. */
function RowOverflow({
  opportunity,
  title,
  onOpenFile,
}: {
  opportunity: PerformanceOpportunity;
  title: string;
  onOpenFile: () => void;
}) {
  const [open, setOpen] = useState(false);
  const item = (label: string, Icon: typeof Copy, onClick: () => void) => (
    <button
      type="button"
      onClick={() => {
        setOpen(false);
        onClick();
      }}
      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {label}
    </button>
  );
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 w-7 px-0 text-[var(--color-text-tertiary)]"
          aria-label={`More actions for ${title}`}
          onClick={(event) => event.stopPropagation()}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-64 p-1"
        onClick={(event) => event.stopPropagation()}
      >
        {item("Open the file", FileCode, onOpenFile)}
        {item("Copy the agent drill-down", Copy, () =>
          copy(agentHandoffCall(opportunity.opportunity_id), "Drill-down call"),
        )}
        {item("Copy the opportunity id", Copy, () => copy(opportunity.opportunity_id, "Opportunity id"))}
      </PopoverContent>
    </Popover>
  );
}
