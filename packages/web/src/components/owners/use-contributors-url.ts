"use client";

import { useCallback, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  formatLeaveParam,
  formatThresholdParam,
  parseLeaveParam,
  parseThresholdParam,
} from "@repowise-dev/ui/owners/offboarding";
import type { OffboardingScenario } from "@repowise-dev/ui/owners/offboarding-view";
import type { OwnerDirectoryFilters, OwnerSortKey } from "@repowise-dev/ui/owners/owner-directory";

const SORT_KEYS: readonly OwnerSortKey[] = [
  "files_owned",
  "hotspots_owned",
  "commit_count_90d",
  "dead_code_lines_owned",
  "bus_factor_risk_files",
];
const DEFAULT_SORT: OwnerSortKey = "files_owned";

/** The Everyone table's filters from `?q=&sort=&automation=1`. */
export function directoryFiltersFromParams(sp: URLSearchParams): OwnerDirectoryFilters {
  const sort = sp.get("sort") as OwnerSortKey | null;
  return {
    q: sp.get("q") ?? "",
    sort: sort && SORT_KEYS.includes(sort) ? sort : DEFAULT_SORT,
    includeAutomation: sp.get("automation") === "1",
  };
}

/** `sp` with the table's filters written in, defaults left out; returns the query string. */
export function writeDirectoryFilters(sp: URLSearchParams, f: OwnerDirectoryFilters): string {
  const next = new URLSearchParams(sp.toString());
  const set = (key: string, value: string | null) => (value ? next.set(key, value) : next.delete(key));
  set("q", f.q.trim() || null);
  set("sort", f.sort === DEFAULT_SORT ? null : f.sort);
  set("automation", f.includeAutomation ? "1" : null);
  return next.toString();
}

/** The table's filters as their query string alone; equal strings mean the same view. */
export function directoryFiltersKey(f: OwnerDirectoryFilters): string {
  return writeDirectoryFilters(new URLSearchParams(), f);
}

/**
 * Filters to adopt from a URL the table did not write itself (a link followed
 * while it stays mounted), or null when the URL is an echo of one of its
 * pending writes (*written*, oldest first) or already says what it shows.
 */
export function externalDirectoryFilters(
  sp: URLSearchParams,
  written: readonly string[],
  shown: OwnerDirectoryFilters,
): OwnerDirectoryFilters | null {
  const incoming = directoryFiltersFromParams(sp);
  const key = directoryFiltersKey(incoming);
  return written.includes(key) || key === directoryFiltersKey(shown) ? null : incoming;
}

export const CONTRIBUTOR_TABS = ["overview", "risks", "loss", "offboarding"] as const;
export type ContributorTab = (typeof CONTRIBUTOR_TABS)[number];

/** Params that belong to one tab and must not ride along to another. */
const SCENARIO_PARAMS = ["leave", "threshold"];
const DIRECTORY_PARAMS = ["q", "sort", "automation"];
const DEFAULT_THRESHOLD_PARAM = formatThresholdParam(parseThresholdParam(null));

function parseTab(raw: string | null): ContributorTab {
  return (CONTRIBUTOR_TABS as readonly string[]).includes(raw ?? "") ? (raw as ContributorTab) : "overview";
}

/** An Ownership page URL: a tab and, for off-boarding, who is leaving. */
export function contributorsHref(base: string, tab: ContributorTab, leave?: string[]): string {
  const sp = new URLSearchParams();
  if (tab !== "overview") sp.set("tab", tab);
  if (leave?.length) sp.set("leave", formatLeaveParam(leave));
  const qs = sp.toString();
  return qs ? `${base}/owners?${qs}` : `${base}/owners`;
}

/**
 * The Ownership page's URL state: `?tab=` and the off-boarding scenario
 * (`?leave=a,b&threshold=50`). The scenario is parsed off the raw strings so
 * its array keeps one identity between renders and the simulation memo holds.
 */
export function useContributorsUrl() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const tab = parseTab(searchParams.get("tab"));
  const rawLeave = searchParams.get("leave");
  const rawThreshold = searchParams.get("threshold");
  const scenario = useMemo<OffboardingScenario>(
    () => ({ leaving: parseLeaveParam(rawLeave), threshold: parseThresholdParam(rawThreshold) }),
    [rawLeave, rawThreshold],
  );

  const replaceParams = useCallback(
    (edit: (sp: URLSearchParams) => void) => {
      const sp = new URLSearchParams(searchParams.toString());
      edit(sp);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  const setTab = useCallback(
    (next: string) =>
      replaceParams((sp) => {
        if (next === "overview") sp.delete("tab");
        else sp.set("tab", next);
        if (next !== "offboarding") for (const p of SCENARIO_PARAMS) sp.delete(p);
        if (next !== "overview") for (const p of DIRECTORY_PARAMS) sp.delete(p);
      }),
    [replaceParams],
  );

  const setScenario = useCallback(
    (next: OffboardingScenario) =>
      replaceParams((sp) => {
        if (next.leaving.length) sp.set("leave", formatLeaveParam(next.leaving));
        else sp.delete("leave");
        const threshold = formatThresholdParam(next.threshold);
        if (threshold === DEFAULT_THRESHOLD_PARAM) sp.delete("threshold");
        else sp.set("threshold", threshold);
      }),
    [replaceParams],
  );

  return { tab, scenario, setTab, setScenario };
}
