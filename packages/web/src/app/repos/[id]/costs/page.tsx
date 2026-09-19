"use client";

import { useCallback, useEffect, useState } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import Link from "next/link";

import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { Skeleton, SkeletonRegion } from "@repowise-dev/ui/ui/skeleton";
import { OverviewSection } from "@repowise-dev/ui/overview/section";
import {
  ACCOUNTING_METHOD_VERSION,
  OpportunityList,
  SavingsLede,
  SavingsMethodology,
  SavingsResetNotice,
  SavingsSourceTable,
  SpendSummary,
  UsageDetails,
  buildOpportunities,
  surfaceLabel,
  type SavingsView,
  type SpendView,
} from "@repowise-dev/ui/savings";
import { getCostSummary, getSavings } from "@/lib/api/costs";
import type { CostSummary, Savings } from "@/lib/api/costs";

/** Where the accounting is written down. One constant, used by the lede, the
 *  reset notice and the methodology section, so the three cannot drift. */
const METHODOLOGY_HREF =
  "https://github.com/repowise-dev/repowise/blob/main/docs/architecture/savings-accounting.md";

const DISTILL_DOCS_HREF =
  "https://github.com/repowise-dev/repowise/blob/main/docs/agent/DISTILL.md";

/** Dismissal is scoped to a repository *and* an accounting-method version, so
 *  a later methodology change announces itself once more to someone who
 *  dismissed the previous one. */
function resetNoticeKey(repoId: string): string {
  return `repowise:savings-reset-dismissed:${repoId}:v${ACCOUNTING_METHOD_VERSION}`;
}

/**
 * Usage and savings: what agents avoided, what Repowise cost, and how we know.
 *
 * A scan surface. Sections and hairlines carry the grouping; the only boxes
 * are the ones a reader can act on, which on this page is none. The subject is
 * the evidence-led lede, and everything below it qualifies or decomposes that
 * one figure.
 *
 * This page performs no accounting arithmetic. `load_report` on the server is
 * the only thing that aggregates or prices, and every figure here is a field
 * off the response. Three surfaces once aggregated the ledger independently
 * and published three different dollar figures for one repository; a number
 * computed in React would be a fourth.
 */
export default function CostsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  // Two fetches above the fold, in one wave. The previous page made five, three
  // of them the same endpoint with a different `by` for tabs that were not
  // open.
  const {
    data: savings,
    error: savingsError,
    isLoading: loadingSavings,
    mutate: retrySavings,
  } = useSWR<Savings>(`savings:${id}`, () => getSavings(id), {
    revalidateOnFocus: false,
  });

  const {
    data: spend,
    error: spendError,
    mutate: retrySpend,
  } = useSWR<CostSummary>(`costs-summary:${id}`, () => getCostSummary(id), {
    revalidateOnFocus: false,
  });

  return (
    <PageShell
      title="Usage & savings"
      description="What agents avoided, what Repowise cost, and how we know."
    >
      <ResetNotice repoId={id} />

      {savingsError ? (
        <ApiError
          title="Couldn't load agent savings"
          message="The savings endpoint did not respond. Model spend below is unaffected."
          onRetry={() => void retrySavings()}
        />
      ) : loadingSavings || savings === undefined ? (
        <SavingsSkeleton />
      ) : (
        <SavingsSections data={savings as SavingsView} />
      )}

      <OverviewSection
        title="Repowise model spend"
        description="What Repowise's own model work cost, reported beside agent savings and never subtracted from them."
      >
        {spendError ? (
          <ApiError title="Couldn't load model spend" onRetry={() => void retrySpend()} />
        ) : spend === undefined ? (
          <p className="text-sm text-[var(--color-text-tertiary)]">Loading model spend…</p>
        ) : (
          <SpendSummary spend={spend as SpendView} />
        )}
      </OverviewSection>

      {/* No `OverviewSection` wrapper: `SavingsMethodology` is a collapsible
          that titles itself, and wrapping it printed "Method and limits"
          twice, once as a heading and once as the toggle under it. */}
      <SavingsMethodology href={METHODOLOGY_HREF} LinkComponent={Link} />
    </PageShell>
  );
}

/**
 * Reserves the lede and ribbon while the report is in flight.
 *
 * Shaped like what replaces it rather than a flat block: the populated lede
 * and ribbon stand ~260px, and a short placeholder made the whole page jump
 * on first paint. Deliberately not `PageSkeleton`, which draws a page header
 * that `PageShell` is already rendering for real just above this.
 */
function SavingsSkeleton() {
  return (
    <SkeletonRegion label="Loading agent savings" className="flex flex-col gap-6">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:gap-12">
        <div className="flex flex-col gap-2.5 lg:w-[220px]">
          <Skeleton className="h-3 w-40" />
          <Skeleton className="h-11 w-32" />
        </div>
        <div className="flex max-w-[62ch] flex-1 flex-col gap-2">
          <Skeleton className="h-[1lh] w-full" />
          <Skeleton className="h-[1lh] w-full" />
          <Skeleton className="h-[1lh] w-2/3" />
        </div>
      </div>
      <div className="grid grid-cols-2 border-y border-[var(--color-border-default)] sm:grid-cols-3 lg:grid-cols-5">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="flex flex-col gap-1.5 px-4 py-3.5">
            <Skeleton className="h-2.5 w-20" />
            <Skeleton className="h-5 w-16" />
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}

/**
 * The savings half of the page, once there is a report to render.
 *
 * `available: false` is a semantic state, not a load state: a repository that
 * has never served an agent legitimately has no savings, and saying so is a
 * different claim from a measured zero.
 */
function SavingsSections({ data }: { data: SavingsView }) {
  if (!data.available) {
    return (
      <EmptyState
        title="No agent savings recorded yet"
        // Plain text: EmptyState renders its description as a string, so
        // backticks would print as backticks.
        description="Route noisy commands through repowise distill, or install the rewrite hook with repowise hook rewrite install. Savings appear here on their own once an agent starts using this repository."
      />
    );
  }

  const opportunities = buildOpportunities(data, {
    distillDocsHref: DISTILL_DOCS_HREF,
  });

  const surfaces = data.per_surface.map((row) => ({
    label: surfaceLabel(row.group),
    events: row.events,
    savedInputTokens: row.saved_input_tokens,
  }));

  return (
    <>
      <SavingsLede data={data} methodologyHref={METHODOLOGY_HREF} LinkComponent={Link} />

      <OverviewSection
        title="Savings by source"
        description="Which capture surface produced each part of the total."
      >
        <SavingsSourceTable
          rows={surfaces}
          total={data.saved_input_tokens}
          nameHeader="Surface"
          caption="Savings by the surface that produced them"
          empty={
            <p className="text-sm text-[var(--color-text-secondary)]">
              No savings in this window carry a surface.
            </p>
          }
        />
      </OverviewSection>

      {opportunities.length > 0 && (
        <OverviewSection
          title="Observed opportunities"
          description="Behaviour that could have been optimised and was not. Never part of the total above."
        >
          <OpportunityList items={opportunities} LinkComponent={Link} />
        </OverviewSection>
      )}

      <OverviewSection
        title="Details"
        description="The same savings, cut by day, operation, pricing model and agent."
      >
        <UsageDetails data={data} />
      </OverviewSection>
    </>
  );
}

/**
 * The savings-reset disclosure, shown once per repository and accounting
 * version.
 *
 * The host owns visibility and persistence; the shared component owns the
 * copy. Starts hidden and reveals after the storage read, so a reader who
 * dismissed this months ago never sees it flash back on every navigation.
 *
 * After dismissal the reset is still disclosed: the lede keeps a permanent
 * coverage date and a methodology link beside the figure they qualify.
 */
function ResetNotice({ repoId }: { repoId: string }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      setVisible(!window.localStorage.getItem(resetNoticeKey(repoId)));
    } catch {
      // Storage unavailable (private mode, blocked site data). Show it: an
      // explanation shown twice is better than a restarted total never
      // explained.
      setVisible(true);
    }
  }, [repoId]);

  const dismiss = useCallback(() => {
    try {
      window.localStorage.setItem(resetNoticeKey(repoId), "1");
    } catch {
      /* Storage unavailable: hide for this session only. */
    }
    setVisible(false);
  }, [repoId]);

  if (!visible) return null;

  return (
    <SavingsResetNotice
      onDismiss={dismiss}
      methodologyHref={METHODOLOGY_HREF}
      LinkComponent={Link}
    />
  );
}
