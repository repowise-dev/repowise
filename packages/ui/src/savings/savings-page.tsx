"use client";

import * as React from "react";

import { formatTokens } from "../lib/format";
import { DEFAULT_SAVINGS_COPY, type SavingsCopy } from "./copy";
import type { SavingsJobId } from "./jobs";
import { RepowiseUsage, type RepowiseUsageProps } from "./repowise-usage";
import { SavingsHero, SavingsProof } from "./savings-hero";
import { SavingsChecks, type SavingsChecksProps } from "./savings-checks";
import { SavingsJobs } from "./savings-jobs";
import { SavingsOverTime } from "./savings-over-time";
import { hasSavings, type SavingsView } from "./types";

const MICRO = "font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-tertiary)]";

export interface SavingsPageBodyProps {
  /** The savings report. Undefined or `available: false` renders an empty
   *  state; a host with spend but no savings events gets the cost-only one. */
  savings: SavingsView | undefined;
  /** Repowise's own spend: summary, grouped rows and the grouping control. */
  usage: Omit<RepowiseUsageProps, "copy">;
  copy?: SavingsCopy;
  /** At most one setup suggestion, rendered under the hero. The host decides
   *  whether there is one; hosted passes none. */
  nudge?: React.ReactNode;
  /** Actions for the empty states, e.g. a copy-prompt button and a setup link. */
  emptyActions?: React.ReactNode;
  /** Jobs whose source is not set up, shown muted when they recorded nothing. */
  notSetUp?: readonly SavingsJobId[];
  /** Connected agents, tool profiles and links for "Checks your agents ran".
   *  The section renders when the report carries check rows. */
  checks?: Omit<SavingsChecksProps, "rows" | "copy" | "LinkComponent">;
  methodologyHref?: string;
  benchmarkHref?: string;
  LinkComponent?: React.ElementType;
}

/**
 * The Usage & savings page body, below the host's header. Props only: it
 * fetches nothing and never asks which host renders it. Leads with how much
 * less agents read, proves it in tokens, files and dollars, then shows the
 * trend, where it came from, what Repowise itself used and how it is counted.
 */
export function SavingsPageBody({
  savings,
  usage,
  copy = DEFAULT_SAVINGS_COPY,
  nudge,
  emptyActions,
  notSetUp,
  checks,
  methodologyHref,
  benchmarkHref,
  LinkComponent,
}: SavingsPageBodyProps) {
  const usageSection = <RepowiseUsage copy={copy} {...usage} />;

  if (!hasSavings(savings)) {
    const costOnly = (usage.summary?.total_calls ?? 0) > 0;
    return (
      <div className="flex flex-col gap-14">
        <SavingsEmpty copy={copy} costOnly={costOnly} actions={emptyActions} />
        {costOnly && usageSection}
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      <SavingsHero data={savings} copy={copy} />
      {nudge && <div className="mt-3.5">{nudge}</div>}
      <div className="mt-8">
        <SavingsProof data={savings} copy={copy} />
      </div>
      <div className="mt-14">
        <SavingsOverTime data={savings} copy={copy} title={copy.overTimeTitle} />
      </div>
      <section className="mt-14">
        <div className="border-b border-[var(--color-border-default)] pb-3">
          <h2 className="text-[18px] font-semibold text-[var(--color-text-primary)]">{copy.jobsTitle}</h2>
          <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">{copy.jobsSub}</p>
        </div>
        <SavingsJobs data={savings} copy={copy} {...(notSetUp ? { notSetUp } : {})} />
      </section>
      {(savings.checks?.length ?? 0) > 0 && (
        <SavingsChecks rows={savings.checks ?? []} copy={copy.checks} LinkComponent={LinkComponent} {...checks} />
      )}
      <div className="mt-14">{usageSection}</div>
      <HowWeCount
        data={savings}
        copy={copy}
        methodologyHref={methodologyHref}
        benchmarkHref={benchmarkHref}
        LinkComponent={LinkComponent}
      />
    </div>
  );
}

function HowWeCount({
  data,
  copy,
  methodologyHref,
  benchmarkHref,
  LinkComponent,
}: {
  data: SavingsView;
  copy: SavingsCopy;
  methodologyHref?: string | undefined;
  benchmarkHref?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";
  const link = "text-[var(--color-accent-primary)] hover:underline";
  return (
    <details className="group mt-14 border-t border-[var(--color-border-default)] pt-4">
      <summary className="flex cursor-pointer list-none justify-between text-[15px] font-semibold text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] [&::-webkit-details-marker]:hidden">
        {copy.methodTitle}
        <span aria-hidden className="font-mono text-[var(--color-text-tertiary)] group-open:hidden">+</span>
        <span aria-hidden className="hidden font-mono text-[var(--color-text-tertiary)] group-open:inline">−</span>
      </summary>
      <div className="mt-3.5 grid grid-cols-1 gap-7 text-xs text-[var(--color-text-secondary)] lg:grid-cols-3">
        <div>
          <h3 className="mb-1 text-xs font-semibold text-[var(--color-text-primary)]">
            {copy.measuredTitle(formatTokens(data.measured_saved_input_tokens ?? 0))}
          </h3>
          <p>{copy.measuredBody}</p>
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold text-[var(--color-text-primary)]">
            {copy.inferredTitle(formatTokens(data.inferred_saved_input_tokens ?? 0))}
          </h3>
          <p>{copy.inferredBody}</p>
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold text-[var(--color-text-primary)]">{copy.pricedTitle}</h3>
          <p>
            {copy.pricedBody}
            {benchmarkHref && (
              <>
                {" "}
                <A href={benchmarkHref} className={link}>
                  {copy.benchmarkLink}
                </A>
              </>
            )}
            {methodologyHref && (
              <>
                {" "}
                <A href={methodologyHref} className={link}>
                  {copy.methodLink}
                </A>
              </>
            )}
          </p>
        </div>
      </div>
    </details>
  );
}

/** No savings to show: a fresh repository, or one with spend but no agent
 *  calls yet (hosted before it records savings). */
export function SavingsEmpty({
  copy = DEFAULT_SAVINGS_COPY,
  costOnly,
  actions,
}: {
  copy?: SavingsCopy;
  costOnly: boolean;
  actions?: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 gap-7 rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-5 py-6 lg:grid-cols-2 lg:gap-12 lg:p-9">
      <div className="min-w-0">
        <div className={MICRO}>{costOnly ? copy.costOnlyMicro : copy.freshMicro}</div>
        <h2 className="mt-2.5 text-[22px] font-semibold tracking-[-0.01em] text-[var(--color-text-primary)]">
          {costOnly ? copy.costOnlyTitle : copy.freshTitle}
        </h2>
        <p className="mt-2.5 max-w-[48ch] text-[15px] text-[var(--color-text-secondary)]">
          {costOnly ? copy.costOnlyBody : copy.freshBody}
        </p>
        {actions && <div className="mt-4 flex flex-wrap items-center gap-3">{actions}</div>}
      </div>
      <div aria-hidden className="grid gap-4 self-center opacity-50">
        {[copy.emptyBase, copy.stripWith].map((label) => (
          <div key={label} className="grid gap-1.5">
            <div className="flex justify-between text-xs text-[var(--color-text-secondary)]">
              <span>{label}</span>
              <span className="font-mono">-</span>
            </div>
            <div className="h-2 rounded bg-[color-mix(in_srgb,var(--color-text-primary)_10%,transparent)]" />
          </div>
        ))}
      </div>
    </div>
  );
}

export interface SavingsNudgeProps {
  title: string;
  body: string;
  /** The copy-prompt control, from the host's prompt modal. */
  action: React.ReactNode;
  href: string;
  linkLabel: string;
  onDismiss: () => void;
  dismissLabel?: string;
  LinkComponent?: React.ElementType;
}

/** One setup suggestion. A dot and a word, not a status pill; the copy-prompt
 *  action is the one primary verb, the setup link the second. */
export function SavingsNudge({
  title,
  body,
  action,
  href,
  linkLabel,
  onDismiss,
  dismissLabel = DEFAULT_SAVINGS_COPY.nudgeDismiss,
  LinkComponent,
}: SavingsNudgeProps) {
  const A = LinkComponent ?? "a";
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-3.5 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-4 py-3.5 lg:grid-cols-[auto_minmax(0,1fr)_auto]">
      <span aria-hidden className="h-2 w-2 rounded-full bg-[var(--color-text-tertiary)]" />
      <div className="min-w-0">
        <b className="text-[15px] font-medium text-[var(--color-text-primary)]">{title}</b>
        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{body}</p>
      </div>
      <div className="col-span-2 flex flex-wrap items-center gap-3 lg:col-span-1 lg:justify-end">
        {action}
        <A href={href} className="text-xs text-[var(--color-accent-primary)] hover:underline">
          {linkLabel}
        </A>
        <button
          type="button"
          onClick={onDismiss}
          aria-label={dismissLabel}
          className="rounded px-1.5 text-base text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          ×
        </button>
      </div>
    </div>
  );
}
