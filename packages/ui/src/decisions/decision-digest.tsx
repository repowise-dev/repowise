"use client";

import * as React from "react";
import type { DecisionRecord } from "@repowise-dev/types/decisions";
import { cn } from "../lib/cn";
import { formatDate, formatNumber, formatRelativeTimeOrNull, parseDateOrNull, stripMarkdown } from "../lib/format";

export interface DecisionDigestProps {
  /**
   * Decisions in force whose code changed after they were accepted
   * (`lane_counts.needs_review`). The row is drawn only above zero.
   */
  needsReview?: number | null;
  /** Narrows the record list to those decisions. Without it the row has no action. */
  onReviewNeedsReview?: () => void;
  /**
   * Churn hotspot files no decision in force names, hottest first
   * (`/decisions/health` `ungoverned_hotspots`). Drawn only when non-empty;
   * omit it where the host cannot read it.
   */
  ungovernedHotspots?: readonly string[] | null;
  /** The action beside that row, such as the host's prompt to record missed decisions. */
  ungovernedAction?: React.ReactNode;
  /** Decisions put in force in the last week, newest first (`lane=recent`). */
  recent?: readonly DecisionRecord[] | null;
  /** How many came into force in the last week, when more exist than `recent` holds. */
  recentTotal?: number | null;
  /** Opens the full Recent section. Offered when `recentTotal` exceeds the rows shown. */
  onShowAllRecent?: () => void;
  /** A recent row's actions, such as its prompt and menu. */
  renderRecentActions?: (decision: DecisionRecord) => React.ReactNode;
  /** Where a recent row's title leads. Without it the title is plain text. */
  recentHref?: (decision: DecisionRecord) => string;
  LinkComponent?: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  className?: string;
}

const ROW = "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 border-b border-[var(--color-border-subtle)] py-2.5";
const META = "col-start-1 flex min-w-0 flex-wrap gap-x-3 gap-y-0.5 text-xs text-[var(--color-text-tertiary)]";
const ACTION_CELL = "col-start-2 row-span-2 row-start-1 flex items-center gap-1.5";
const HEAD = "flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1";
const H = "text-base font-semibold text-[var(--color-text-primary)]";
const TEXT_BUTTON =
  "whitespace-nowrap rounded text-xs text-[var(--color-accent-primary)] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

/**
 * The decisions page's digest, for the person auditing what agents maintain:
 * what needs someone, and what came into force this week.
 *
 * Presentational. Every figure is one the host measured and passes in; a row
 * is drawn only for a state that needs someone, so a healthy repository shows
 * no "Needs attention" at all.
 */
export function DecisionDigest({
  needsReview,
  onReviewNeedsReview,
  ungovernedHotspots,
  ungovernedAction,
  recent,
  recentTotal,
  onShowAllRecent,
  renderRecentActions,
  recentHref,
  LinkComponent = "a",
  className,
}: DecisionDigestProps) {
  const moved = needsReview ?? 0;
  const hot = ungovernedHotspots ?? [];
  const attention = moved > 0 || hot.length > 0;
  const recentRows = recent ?? [];
  if (!attention && recentRows.length === 0) return null;
  return (
    <div className={cn("grid gap-8", className)}>
      {attention && (
        <NeedsAttention
          moved={moved}
          onReviewNeedsReview={onReviewNeedsReview}
          hot={hot}
          ungovernedAction={ungovernedAction}
        />
      )}
      {recentRows.length > 0 && (
        <ThisWeek
          rows={recentRows}
          total={recentTotal}
          onShowAll={onShowAllRecent}
          renderActions={renderRecentActions}
          href={recentHref}
          LinkComponent={LinkComponent}
        />
      )}
    </div>
  );
}

function NeedsAttention({
  moved,
  onReviewNeedsReview,
  hot,
  ungovernedAction,
}: {
  moved: number;
  onReviewNeedsReview?: (() => void) | undefined;
  hot: readonly string[];
  ungovernedAction?: React.ReactNode;
}) {
  const id = React.useId();
  return (
    <section aria-labelledby={id} className="grid gap-2.5">
      <div className={HEAD}>
        <h2 id={id} className={H}>
          Needs attention
        </h2>
      </div>
      <div className="border-t border-[var(--color-border-subtle)]">
        {moved > 0 && <MovedRow moved={moved} onReview={onReviewNeedsReview} />}
        {hot.length > 0 && <HotspotRow hot={hot} action={ungovernedAction} />}
      </div>
    </section>
  );
}

function MovedRow({ moved, onReview }: { moved: number; onReview?: (() => void) | undefined }) {
  const one = moved === 1;
  return (
    <div className={ROW}>
      <p className="min-w-0 text-sm text-[var(--color-text-primary)]">
        <Dot tone="warning" />
        <span className="font-mono font-semibold tabular-nums">{formatNumber(moved)}</span>{" "}
        {one
          ? "decision in force names code that changed after it was accepted."
          : "decisions in force name code that changed after they were accepted."}
      </p>
      <p className={META}>
        Agents still hear {one ? "it" : "them"}. Check {one ? "it holds" : "they hold"}, or hand each one to your agent from its menu.
      </p>
      {onReview && (
        <div className={ACTION_CELL}>
          <button type="button" className={TEXT_BUTTON} onClick={onReview}>
            Review {formatNumber(moved)}
          </button>
        </div>
      )}
    </div>
  );
}

function HotspotRow({ hot, action }: { hot: readonly string[]; action?: React.ReactNode }) {
  return (
    <div className={ROW}>
      <p className="min-w-0 text-sm text-[var(--color-text-primary)]">
        <Dot tone="neutral" />
        <span className="font-mono font-semibold tabular-nums">{formatNumber(hot.length)}</span>{" "}
        {hot.length === 1
          ? "churn hotspot file has no decision in force."
          : "churn hotspot files have no decision in force."}
      </p>
      <p className={META}>
        {hot.slice(0, 2).map((path) => (
          <span key={path} className="font-mono text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
            {path}
          </span>
        ))}
        {hot.length > 2 && <span className="font-mono tabular-nums">+{formatNumber(hot.length - 2)}</span>}
      </p>
      {action && <div className={ACTION_CELL}>{action}</div>}
    </div>
  );
}

function ThisWeek({
  rows,
  total,
  onShowAll,
  renderActions,
  href,
  LinkComponent,
}: {
  rows: readonly DecisionRecord[];
  total?: number | null | undefined;
  onShowAll?: (() => void) | undefined;
  renderActions?: ((decision: DecisionRecord) => React.ReactNode) | undefined;
  href?: ((decision: DecisionRecord) => string) | undefined;
  LinkComponent: NonNullable<DecisionDigestProps["LinkComponent"]>;
}) {
  const id = React.useId();
  return (
    <section aria-labelledby={id} className="grid gap-2.5">
      <div className={HEAD}>
        <h2 id={id} className={H}>
          This week
        </h2>
        <span className="text-xs text-[var(--color-text-tertiary)]">Put in force in the last 7 days</span>
      </div>
      <ul className="border-t border-[var(--color-border-subtle)]">
        {rows.map((d) => (
          <RecentRow key={d.id} d={d} actions={renderActions?.(d)} href={href?.(d)} LinkComponent={LinkComponent} />
        ))}
      </ul>
      {onShowAll && total != null && total > rows.length && (
        <div>
          <button type="button" className={TEXT_BUTTON} onClick={onShowAll}>
            See all {formatNumber(total)} in Recent
          </button>
        </div>
      )}
    </section>
  );
}

function RecentRow({
  d,
  actions,
  href,
  LinkComponent,
}: {
  d: DecisionRecord;
  actions: React.ReactNode;
  href: string | undefined;
  LinkComponent: NonNullable<DecisionDigestProps["LinkComponent"]>;
}) {
  const r = d.recent;
  const title = stripMarkdown(d.title);
  const when = r?.at ?? d.created_at;
  return (
    // The group lets the row's prompt reveal on hover and focus, as in the table.
    <li className={cn(ROW, "group/row")}>
      <p className="min-w-0 text-sm text-[var(--color-text-primary)]">
        {href ? (
          <LinkComponent
            href={href}
            className="underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
          >
            {title}
          </LinkComponent>
        ) : (
          title
        )}
      </p>
      <p className={META}>
        {r ? <span>{r.signer_kind === "agent" ? `${r.signer} (agent)` : r.signer}</span> : null}
        {r?.commit ? <span className="font-mono">{r.commit.slice(0, 9)}</span> : null}
        {parseDateOrNull(when) ? (
          <span className="tabular-nums" title={formatDate(when)}>
            {formatRelativeTimeOrNull(when)}
          </span>
        ) : null}
      </p>
      {actions ? <div className={ACTION_CELL}>{actions}</div> : null}
    </li>
  );
}

function Dot({ tone }: { tone: "warning" | "neutral" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "mr-2 inline-block h-[7px] w-[7px] rounded-full align-[1px]",
        tone === "warning" ? "bg-[var(--color-warning)]" : "bg-[var(--color-text-tertiary)]",
      )}
    />
  );
}
