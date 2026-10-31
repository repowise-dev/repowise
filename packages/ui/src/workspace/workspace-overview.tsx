import type { ReactNode } from "react";

import { healthMark, healthMarkTextColor, type HealthMark } from "../health/tokens";
import { formatNumber } from "../lib/format";
import type { RibbonStat } from "../stats/stat-ribbon";
import { ContractTypeBadge } from "./contract-type-badge";

/**
 * The workspace overview's section order, shared so every host renders the
 * same page: the lede (how many repositories, and which need fixing), the
 * cross-repo ribbon, the repositories, "Do next" folded to one line, then
 * contracts and co-changes.
 *
 * "Do next" used to sit above the repositories and, open, ran two actions per
 * repository, which pushed the list the page exists for below the first
 * screen. It sits after them because its actions belong to them.
 *
 * Slots, not data: each host fetches and words its own sections. A slot left
 * out renders nothing. No `"use client"`, so a server page stays a server page.
 */
export function WorkspaceOverviewSections({
  lede,
  ribbon,
  repositories,
  nextActions,
  contracts,
  coChanges,
}: {
  lede?: ReactNode;
  ribbon?: ReactNode;
  repositories: ReactNode;
  nextActions?: ReactNode;
  contracts?: ReactNode;
  coChanges?: ReactNode;
}) {
  return (
    <>
      {lede}
      {ribbon}
      {repositories}
      {nextActions}
      {contracts}
      {coChanges}
    </>
  );
}

export interface LowestRepoHealth {
  name: string;
  /** 0-10. */
  score: number;
  mark: HealthMark;
  /** Scored repositories in the healthy band, out of `scored`. */
  healthy: number;
  scored: number;
}

/**
 * The weakest scored repository, and how many scored ones sit in the healthy
 * band. Null when no repository has a score yet: an unmeasured workspace is
 * not one whose lowest health is zero.
 *
 * `health` is 0-10, as `RepoRows` reads it, so the cell and the row it names
 * take the same band.
 */
export function lowestRepoHealth(
  repos: ReadonlyArray<{ name: string; health: number | null }>,
): LowestRepoHealth | null {
  const scored = repos.filter((r): r is { name: string; health: number } => r.health != null);
  if (scored.length === 0) return null;
  const low = scored.reduce((a, b) => (b.health < a.health ? b : a));
  return {
    name: low.name,
    score: low.health,
    mark: healthMark(low.health),
    healthy: scored.filter((r) => healthMark(r.health).band === "healthy").length,
    scored: scored.length,
  };
}

/**
 * The ribbon cell for {@link lowestRepoHealth}. The host words it, so a
 * translated page passes its own strings.
 */
export function lowestHealthStat(
  low: LowestRepoHealth | null,
  copy: {
    label: string;
    /** Sub-line under the figure, e.g. "backend · 2 of 3 scored repositories healthy". */
    sub: (low: LowestRepoHealth) => string;
    /** Sub-line when nothing is scored. */
    none: string;
  },
): RibbonStat {
  return {
    label: copy.label,
    value: low ? low.mark.value : "—",
    valueColor: low ? healthMarkTextColor(low.score) : undefined,
    sub: low ? copy.sub(low) : copy.none,
  };
}

/** Contracts per type, largest first, each badge beside its count. */
export function ContractTypeCounts({ byType }: { byType: Readonly<Record<string, number>> }) {
  const types = Object.entries(byType).sort((a, b) => b[1] - a[1]);
  return (
    // Badge and figure sit together instead of spanning the cell:
    // justify-between across a quarter of 1280px pushed them so far apart
    // they stopped reading as one pair.
    <dl className="m-0 flex flex-wrap gap-x-10 gap-y-3">
      {types.map(([type, count]) => (
        <div key={type} className="flex items-center gap-2.5">
          <dt>
            <ContractTypeBadge type={type} />
          </dt>
          <dd className="text-sm font-medium tabular-nums text-[var(--color-text-primary)]">
            {formatNumber(count)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
