import type { Metadata } from "next";
import { GitMerge } from "lucide-react";
import { getTranslations } from "next-intl/server";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/api-client/types";
import { PageShell } from "@repowise-dev/ui/shared";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import type { RepoPairSummary } from "@repowise-dev/ui/workspace/repo-pair-table";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { repoPairId, STRENGTH_DEFINITION } from "@repowise-dev/ui/workspace/co-change-facts";
import { getWorkspace, getWorkspaceCoChanges } from "@/lib/api/workspace";
import { CoChangesExplorer } from "./co-changes-explorer";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("coChanges");
  return { title: t("title") };
}

export const revalidate = 30;

/** The endpoint's ceiling. Asked for in full so the table is not a second,
 *  narrower cap on top of the one the miner already applied. */
const ROW_LIMIT = 500;

type Props = {
  searchParams: Promise<{ pair?: string; q?: string; cc?: string }>;
};

/**
 * Cross-repo co-changes.
 *
 * The server renders the figures; everything that responds to the reader (the
 * repository-pair narrowing, the path search and the pair drawer) lives in one
 * client leaf, which filters the rows it was handed instead of asking the
 * server again. The URL still carries all three, so a narrowed list or an open
 * pair is a linkable address.
 */
export default async function CoChangesPage({ searchParams }: Props) {
  const t = await getTranslations("coChanges");
  const { pair, q, cc } = await searchParams;

  // One wave. The workspace call is what the layout already makes, and it
  // gives the alias-to-repo-id map the drawer's file links need.
  const [res, ws] = await Promise.allSettled([
    getWorkspaceCoChanges({ limit: ROW_LIMIT }),
    getWorkspace(),
  ]);
  const data = res.status === "fulfilled" ? res.value : null;
  const coChanges = data?.co_changes ?? [];
  // What the miner found before its own caps trimmed the artifact. Above
  // coChanges.length means this page is the top of a longer list.
  const totalMined = data?.total_mined ?? coChanges.length;
  const repoIds: Record<string, string> = {};
  if (ws.status === "fulfilled") {
    for (const r of ws.value.repos) if (r.repo_id) repoIds[r.alias] = r.repo_id;
  }

  const icon = <GitMerge className="h-5 w-5 text-[var(--color-text-tertiary)]" />;
  const description = t("description");

  if (coChanges.length === 0) {
    return (
      <PageShell title={t("title")} icon={icon} description={description}>
        <EmptyState
          title={t("emptyTitle")}
          description={t("emptyDescription")}
          icon={<GitMerge className="h-8 w-8" />}
        />
      </PageShell>
    );
  }

  const repoPairs = summarisePairs(coChanges);
  const strongest = repoPairs[0] ?? null;
  const mostRecent = coChanges.reduce<string>(
    (latest, c) => (c.last_date > latest ? c.last_date : latest),
    "",
  );
  const freqs = coChanges.map((c) => c.frequency);
  const minFreq = Math.min(...freqs);
  const maxFreq = Math.max(...freqs);

  // The lede owns the file-pair count, so the ribbon does not repeat it.
  const ribbon: RibbonStat[] = [
    {
      label: t("ribbon.repoPairs"),
      value: String(repoPairs.length),
      sub: t("ribbon.repoPairsSub"),
    },
    {
      label: t("ribbon.strongest"),
      value: strongest ? `${strongest.repo1} and ${strongest.repo2}` : "",
      sub: t("ribbon.strongestSub"),
    },
    {
      label: t("ribbon.peakStrength"),
      value: strongest ? `${Math.round(strongest.maxStrength * 100)}%` : "",
      hint: STRENGTH_DEFINITION,
      sub: t("ribbon.peakStrengthSub"),
    },
    {
      label: t("ribbon.sessionsPerPair"),
      value:
        minFreq === maxFreq
          ? String(minFreq)
          : t("ribbon.sessionsRange", { min: minFreq, max: maxFreq }),
      sub: t("ribbon.sessionsPerPairSub"),
    },
    {
      label: t("ribbon.mostRecent"),
      value: mostRecent ? mostRecent.slice(0, 10) : "",
      sub: t("ribbon.mostRecentSub"),
    },
  ];

  return (
    <PageShell title={t("title")} icon={icon} description={description}>
      <PageLede
        label={t("ledeLabel")}
        value={formatNumber(coChanges.length)}
        unit={
          totalMined > coChanges.length
            ? t("ledeUnitOfFound", { total: formatNumber(totalMined) })
            : t("ledeUnitFound")
        }
        layout="beside"
      >
        <p>{t("ledeSentence")}</p>
      </PageLede>

      <StatRibbon stats={ribbon} />

      <CoChangesExplorer
        coChanges={coChanges}
        repoPairs={repoPairs}
        totalMined={totalMined}
        caps={{
          truncatedBy: data?.truncated_by ?? null,
          perRepoPairCap: data?.per_repo_pair_cap ?? null,
          totalCap: data?.total_cap ?? null,
        }}
        repoIds={repoIds}
        initialPair={pair && repoPairs.some((p) => p.id === pair) ? pair : null}
        initialQuery={q ?? ""}
        initialOpen={cc ?? null}
      />
    </PageShell>
  );
}

/** Roll file pairs up to the repository pairs they connect. */
function summarisePairs(coChanges: WorkspaceCoChangeEntry[]): RepoPairSummary[] {
  const map = new Map<string, RepoPairSummary>();
  for (const c of coChanges) {
    const [repo1, repo2] = [c.source_repo, c.target_repo].sort() as [string, string];
    const id = repoPairId(repo1, repo2);
    const summary = map.get(id) ?? {
      id,
      repo1,
      repo2,
      filePairCount: 0,
      maxStrength: 0,
      lastDate: "",
    };
    summary.filePairCount += 1;
    if (c.strength > summary.maxStrength) summary.maxStrength = c.strength;
    if (c.last_date > summary.lastDate) summary.lastDate = c.last_date;
    map.set(id, summary);
  }
  return [...map.values()].sort((a, b) => b.maxStrength - a.maxStrength);
}
