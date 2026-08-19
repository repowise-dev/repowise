import type { Metadata } from "next";
import { GitMerge } from "lucide-react";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/api-client/types";
import { PageShell } from "@repowise-dev/ui/shared";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { CoChangeTable } from "@repowise-dev/ui/workspace/co-change-table";
import type { RepoPairSummary } from "@repowise-dev/ui/workspace/repo-pair-table";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { getWorkspaceCoChanges } from "@/lib/api/workspace";
import { RepoPairLinks } from "./repo-pair-links";

export const metadata: Metadata = { title: "Co-changes" };

export const revalidate = 30;

/** The endpoint's ceiling. Asked for in full so the table is not a second,
 *  narrower cap on top of the one the miner already applied. */
const ROW_LIMIT = 500;

type Props = {
  searchParams: Promise<{ pair?: string }>;
};

/**
 * Cross-repo co-changes.
 *
 * The three `MetricCard`s here were the clearest case in the workspace set of
 * figures that should not exist rather than figures that needed restyling:
 *
 *   - "Avg Strength" averaged a heuristic across unrelated repository pairs,
 *     over a list already sorted by that same heuristic and cut at 100 rows.
 *     It could only ever report a high number. Peak strength replaces it,
 *     because a maximum survives truncation that keeps the strongest rows.
 *   - "Total Co-Change Pairs" reported the miner's own per-pair cap as though
 *     it were a count of what exists.
 *   - "Repo Pairs" was derived from the loaded page rather than the total.
 *
 * The view toggle and its drill-down went too: the rollup and the file pairs
 * are two altitudes of one subject, so they are two sections now, and choosing
 * a pair narrows the lower one instead of replacing the page.
 */
export default async function CoChangesPage({ searchParams }: Props) {
  const { pair } = await searchParams;

  const res = await Promise.allSettled([getWorkspaceCoChanges({ limit: ROW_LIMIT })]);
  const data = res[0].status === "fulfilled" ? res[0].value : null;
  const coChanges = data?.co_changes ?? [];
  // What the miner found before its own caps trimmed the artifact. Above
  // coChanges.length means this page is the top of a longer list.
  const totalMined = data?.total_mined ?? coChanges.length;

  const repoPairs = summarisePairs(coChanges);
  const selected = pair && repoPairs.some((p) => p.id === pair) ? pair : null;
  const filePairs = selected
    ? coChanges.filter((cc) => pairId(cc) === selected)
    : coChanges;

  const strongest = repoPairs[0] ?? null;
  const mostRecent = coChanges.reduce<string>(
    (latest, cc) => (cc.last_date > latest ? cc.last_date : latest),
    "",
  );

  const ribbon: RibbonStat[] = [
    {
      label: "Repository pairs",
      value: coChanges.length > 0 ? String(repoPairs.length) : "—",
      sub: "with at least one shared work session",
    },
    {
      label: "File pairs",
      value: coChanges.length > 0 ? formatNumber(coChanges.length) : "—",
      sub:
        totalMined > coChanges.length
          ? `of ${formatNumber(totalMined)} found; capped by the miner`
          : "every pair the miner found",
    },
    {
      label: "Strongest pair",
      value: strongest ? `${strongest.repo1} ↔ ${strongest.repo2}` : "—",
      sub: "most tightly coupled by work pattern",
    },
    {
      label: "Peak strength",
      value: strongest ? `${Math.round(strongest.maxStrength * 100)}%` : "—",
      sub: "the single strongest file pair",
    },
    {
      label: "Most recent",
      value: mostRecent ? mostRecent.slice(0, 10) : "—",
      sub: "last commit touching a linked pair",
    },
  ];

  if (coChanges.length === 0) {
    return (
      <PageShell
        title="Co-changes"
        icon={<GitMerge className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
        description="Files in different repositories that recent commits touched together."
      >
        <EmptyState
          title="No cross-repo co-changes found"
          description="Co-changes are mined from each repository's git history during a workspace sync. Pairs appear once commits close together in time touch files in two repositories."
          icon={<GitMerge className="h-8 w-8" />}
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title="Co-changes"
      icon={<GitMerge className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
      description="Files in different repositories that recent commits touched together."
    >
      <PageLede
        label="Repository pairs"
        value={String(repoPairs.length)}
        unit="change together"
        layout="beside"
      >
        <p>
          {formatNumber(coChanges.length)} file pairs across{" "}
          {repoPairs.length === 1 ? "one repository pair" : `${repoPairs.length} repository pairs`}{" "}
          were touched in the same work sessions.
          {strongest && (
            <>
              {" "}
              {strongest.repo1} and {strongest.repo2} are the most tightly coupled, peaking at{" "}
              {Math.round(strongest.maxStrength * 100)}%.
            </>
          )}
        </p>
        <p>
          Strength is the share of the less-active file&rsquo;s recent sessions that also touched
          its partner. This is a work-pattern signal from git history, not a declared or verified
          dependency, so it is a place to start looking rather than proof of coupling.
          {totalMined > coChanges.length ? (
            <>
              {" "}
              The miner found {formatNumber(totalMined)} qualifying file pairs and kept the
              strongest {formatNumber(coChanges.length)}, so this is the top of the list rather
              than all of it.
            </>
          ) : (
            " Every pair the miner found is shown."
          )}
        </p>
      </PageLede>

      <StatRibbon stats={ribbon} />

      <OverviewSection
        title="Repository pairs"
        description="Ranked by their strongest file pair. Choose one to narrow the files below; choose it again to clear."
      >
        <RepoPairLinks repoPairs={repoPairs} />
      </OverviewSection>

      <OverviewSection
        title={selected ? `Files in ${selected.replace("↔", " and ")}` : "Files that change together"}
        description={
          selected
            ? `${formatNumber(filePairs.length)} file ${filePairs.length === 1 ? "pair" : "pairs"} in this repository pair, strongest first.`
            : `All ${formatNumber(filePairs.length)} file pairs, strongest first.`
        }
      >
        <CoChangeTable coChanges={filePairs} />
      </OverviewSection>
    </PageShell>
  );
}

/** Stable id for a repo pair, order-independent so A→B and B→A are one pair. */
function pairId(cc: WorkspaceCoChangeEntry): string {
  const [a, b] = [cc.source_repo, cc.target_repo].sort();
  return `${a}↔${b}`;
}

/** Roll file pairs up to the repository pairs they connect. */
function summarisePairs(coChanges: WorkspaceCoChangeEntry[]): RepoPairSummary[] {
  const map = new Map<string, RepoPairSummary>();
  for (const cc of coChanges) {
    const [repo1, repo2] = [cc.source_repo, cc.target_repo].sort();
    const id = `${repo1}↔${repo2}`;
    const summary = map.get(id) ?? {
      id,
      repo1,
      repo2,
      filePairCount: 0,
      maxStrength: 0,
      lastDate: "",
    };
    summary.filePairCount += 1;
    if (cc.strength > summary.maxStrength) summary.maxStrength = cc.strength;
    if (cc.last_date > summary.lastDate) summary.lastDate = cc.last_date;
    map.set(id, summary);
  }
  return [...map.values()].sort((a, b) => b.maxStrength - a.maxStrength);
}
