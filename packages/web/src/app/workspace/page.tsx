import type { ReactNode } from "react";
import type { Metadata } from "next";
import Link from "next/link";
import { Layers } from "lucide-react";
import type { WorkspaceRepoEntry } from "@repowise-dev/api-client/types";
import { PageShell } from "@repowise-dev/ui/shared";
import { PageLede, LedeLink } from "@repowise-dev/ui/shared/page-lede";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import {
  OverviewSection,
  SectionLink,
  RepoRows,
  type RepoRow,
} from "@repowise-dev/ui/overview";
// Direct path, not the `ui/stats` barrel: that barrel re-exports two
// "use client" modules, which would drag a hydration boundary into this
// server page for a component that has no state.
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { CoChangeTable } from "@repowise-dev/ui/workspace/co-change-table";
import { ContractTypeBadge } from "@repowise-dev/ui/workspace/contract-type-badge";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { getWorkspace, getWorkspaceCoChanges } from "@/lib/api/workspace";
import { SyncButton } from "./sync-buttons";

export const metadata: Metadata = { title: "Workspace" };

export const revalidate = 30;

/** Co-change rows shown inline. The full list has its own page, and this
 *  figure is never reported as a total, so the cap cannot be read as one. */
const COCHANGE_PREVIEW = 8;

/**
 * The workspace overview.
 *
 * It used to open with five cross-repo `MetricCard`s and put the repositories
 * themselves in a grid of cards underneath, which is the same shape the
 * multi-repo dashboard carried before #1578. Three of those figures were
 * rebuilt rather than restyled:
 *
 *   - "Avg Coverage" averaged per-repo percentages unweighted, so a 50-file
 *     repo at 100% and a 5,000-file repo at 10% reported 55%. It is weighted by
 *     page count now, and named for what the rest of the app calls it.
 *   - The cross-repo force-directed graph drew seven nodes and thirteen edges
 *     on a real workspace — a list wearing a canvas — and cost a third request
 *     that swept every repo's sqlite a second time. The system map owns that
 *     view; this page links to it.
 *   - The repo grid became `RepoRows`, so a repository's health leads the row
 *     that decides whether you open it.
 */
export default async function WorkspaceDashboardPage() {
  // One wave, both on the server. The graph section that used to sit here was
  // a client component fetching after mount, which waterfalled a second
  // per-repo sqlite sweep in behind the paint.
  const [ws, cc] = await Promise.allSettled([
    getWorkspace(),
    getWorkspaceCoChanges({ limit: COCHANGE_PREVIEW }),
  ]);

  const workspace = ws.status === "fulfilled" ? ws.value : null;
  const coChanges = cc.status === "fulfilled" ? cc.value : null;
  const repos = workspace?.repos ?? [];
  const crossRepo = workspace?.cross_repo_summary ?? null;
  const contracts = workspace?.contract_summary ?? null;

  if (repos.length === 0) {
    return (
      <PageShell
        title={workspace?.workspace_name ?? "Workspace"}
        icon={<Layers className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
        description="Every repository registered in this workspace, and what connects them."
      >
        <EmptyState
          title="No repositories discovered yet"
          description="Run `repowise init .` in the workspace root to scan for git repositories and index them. They show up here as soon as the scan lands."
          icon={<Layers className="h-8 w-8" />}
        />
      </PageShell>
    );
  }

  const totals = repos.reduce(
    (acc, r) => ({
      files: acc.files + r.file_count,
      symbols: acc.symbols + r.symbol_count,
      pages: acc.pages + r.page_count,
      // Page-weighted, so a small repo cannot pull the workspace average.
      confidenceWeighted: acc.confidenceWeighted + r.doc_coverage_pct * r.page_count,
      hotspots: acc.hotspots + r.hotspot_count,
    }),
    { files: 0, symbols: 0, pages: 0, confidenceWeighted: 0, hotspots: 0 },
  );
  // Floored, not rounded: 99.7% rounds to a perfect 100% and tells the reader
  // every page is current when some are not. Erring low never overclaims.
  const docFreshness =
    totals.pages > 0 ? Math.floor(totals.confidenceWeighted / totals.pages) : null;

  const ribbon: RibbonStat[] = [
    {
      label: "Files",
      value: formatNumber(totals.files),
      sub: `across ${repos.length} ${repos.length === 1 ? "repository" : "repositories"}`,
    },
    {
      label: "Symbols",
      value: formatNumber(totals.symbols),
      sub: "functions, classes and methods",
    },
    {
      label: "Doc freshness",
      value: docFreshness === null ? "—" : `${docFreshness}%`,
      sub:
        docFreshness === null
          ? "no documentation generated yet"
          : `weighted across ${formatNumber(totals.pages)} pages`,
    },
    {
      label: "Hotspots",
      value: formatNumber(totals.hotspots),
      sub: "files by churn and prior fixes",
    },
    {
      label: "Contract links",
      value: contracts ? formatNumber(contracts.total_links) : "—",
      // No href: a linked ribbon cell drops its `sub`, and the sentence under
      // the figure is worth more than a second route to the section below,
      // which already carries its own link.
      sub: contracts ? "matched provider to consumer" : "no contract data yet",
    },
  ];

  const contractTypes = Object.entries(contracts?.by_type ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <PageShell
      title={workspace?.workspace_name ?? "Workspace"}
      icon={<Layers className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
      description="Every repository registered in this workspace, and what connects them."
      actions={<SyncButton variant="primary" label="Sync workspace" />}
    >
      <PageLede
        label="Repositories"
        value={String(repos.length)}
        unit="in this workspace"
        layout="beside"
        action={<LedeLink href="/workspace/system-map" LinkComponent={Link}>See the system map</LedeLink>}
      >
        <p>
          {formatNumber(totals.files)} files and {formatNumber(totals.symbols)} symbols are under
          intelligence here
          {totals.pages > 0
            ? `, with ${formatNumber(totals.pages)} documentation pages written from them`
            : ""}
          .{" "}
          {workspace?.workspace_root && (
            <>
              Everything is rooted at{" "}
              <span className="font-mono text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
                {workspace.workspace_root}
              </span>
              .
            </>
          )}
        </p>
        <p>{attentionSentence(repos)}</p>
        {unregisteredNote(repos, crossRepo)}
      </PageLede>

      <StatRibbon stats={ribbon} LinkComponent={Link} />

      <OverviewSection
        title="Repositories"
        description="Ordered by what needs attention first — never indexed, then missing on disk, then by health score — rather than by name."
      >
        <RepoRows
          repos={repos.slice().sort(byAttention).map(toRow)}
          LinkComponent={Link}
          actionsFor={(repo) =>
            repo.status === "missing_dir" ? null : (
              <SyncButton
                alias={repo.id}
                label={repo.status === "indexed" ? "Sync" : "Index now"}
              />
            )
          }
        />
      </OverviewSection>

      {contracts && contracts.total_contracts > 0 && (
        <OverviewSection
          title="Contracts"
          description="Routes, topics and tables one repository publishes and another consumes, matched across the workspace."
          action={
            <SectionLink href="/workspace/contracts" LinkComponent={Link}>
              All contracts
            </SectionLink>
          }
        >
          {/* Badge and figure sit together rather than spanning the cell:
              justify-between across a quarter of 1280px pushed them so far
              apart they stopped reading as one pair. */}
          <dl className="m-0 flex flex-wrap gap-x-10 gap-y-3">
            {contractTypes.map(([type, count]) => (
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
        </OverviewSection>
      )}

      {coChanges && coChanges.co_changes.length > 0 && (
        <OverviewSection
          title="Files that change together"
          description="Cross-repo file pairs that recent commits touched in the same session. A work-pattern signal mined from git history, not a declared dependency."
          action={
            <SectionLink href="/workspace/co-changes" LinkComponent={Link}>
              All co-changes
            </SectionLink>
          }
        >
          <CoChangeTable coChanges={coChanges.co_changes} compact />
        </OverviewSection>
      )}
    </PageShell>
  );
}

/** Status the server may not have stamped yet, derived the same way everywhere. */
function statusOf(repo: WorkspaceRepoEntry): "indexed" | "needs_index" | "missing_dir" {
  return repo.status ?? (repo.repo_id ? "indexed" : "needs_index");
}

/**
 * Worst first: never indexed, then missing on disk, then lowest health.
 * Mirrors the dashboard's ordering so the two lists read the same way.
 */
function byAttention(a: WorkspaceRepoEntry, b: WorkspaceRepoEntry): number {
  const rank = (r: WorkspaceRepoEntry) => {
    const s = statusOf(r);
    if (s === "needs_index") return 0;
    if (s === "missing_dir") return 1;
    return 2;
  };
  const byRank = rank(a) - rank(b);
  if (byRank !== 0) return byRank;
  // A repo with no score sorts after scored ones rather than as a zero.
  const ha = a.health_score ?? Number.POSITIVE_INFINITY;
  const hb = b.health_score ?? Number.POSITIVE_INFINITY;
  return ha - hb;
}

function toRow(repo: WorkspaceRepoEntry): RepoRow {
  return {
    id: repo.alias,
    name: repo.alias,
    localPath: repo.path,
    href: repo.repo_id ? `/repos/${repo.repo_id}/overview` : "/workspace",
    status: statusOf(repo),
    // The wire carries 0-100; RepoRows and `bandForScore` are both 0-10.
    // Optional on the type, so a web build ahead of its server shows "—"
    // rather than NaN.
    health: repo.health_score == null ? null : repo.health_score / 10,
    fileCount: repo.file_count,
    hotspotCount: repo.hotspot_count,
    // The workspace payload has no per-repo fresh-page or dead-export counts.
    // Zero here means the row omits those clauses; it never prints "0% fresh",
    // which would report a missing measurement as a bad one.
    docPageCount: 0,
    docFreshPageCount: 0,
    deadExportCount: 0,
    updatedAt: repo.indexed_at,
    indexBehind: null,
  };
}

/** One sentence naming what needs doing, or confirming nothing does. */
function attentionSentence(repos: WorkspaceRepoEntry[]): string {
  const names = (list: WorkspaceRepoEntry[]) =>
    list.length <= 3
      ? list.map((r) => r.alias).join(", ")
      : `${list
          .slice(0, 3)
          .map((r) => r.alias)
          .join(", ")} and ${list.length - 3} more`;

  const parts: string[] = [];
  const needsIndex = repos.filter((r) => statusOf(r) === "needs_index");
  const missing = repos.filter((r) => statusOf(r) === "missing_dir");
  const docsSkipped = repos.filter((r) => r.docs_skip_reason);

  if (needsIndex.length > 0) {
    parts.push(
      `${needsIndex.length} ${needsIndex.length === 1 ? "repository has" : "repositories have"} not been indexed yet (${names(needsIndex)})`,
    );
  }
  if (missing.length > 0) {
    parts.push(
      `${missing.length} ${missing.length === 1 ? "directory is" : "directories are"} missing on disk (${names(missing)})`,
    );
  }
  if (docsSkipped.length > 0) {
    parts.push(
      `documentation was skipped for ${docsSkipped.length} (${names(docsSkipped)})`,
    );
  }

  if (parts.length === 0) {
    return "Every registered repository is indexed and present on disk.";
  }
  return `${parts.join("; ")}. Each is marked in the list below.`;
}

/**
 * Name repositories the cross-repo artifacts cover but the config no longer
 * registers.
 *
 * The two halves of this page come from different sources: the repo list is
 * read from `.repowise-workspace.yaml` while every cross-repo figure is folded
 * out of the persisted artifacts. When the config loses a repo, the page shows
 * a small repo count directly above figures spanning more of them, and nothing
 * says why. Saying it is cheap; the disagreement is itself the signal.
 */
function unregisteredNote(
  repos: WorkspaceRepoEntry[],
  crossRepo: { top_connections: Array<{ repos: string[] }> } | null,
): ReactNode {
  if (!crossRepo) return null;
  const registered = new Set(repos.map((r) => r.alias));
  const seen = new Set<string>();
  for (const c of crossRepo.top_connections) {
    for (const alias of c.repos) {
      if (!registered.has(alias)) seen.add(alias);
    }
  }
  if (seen.size === 0) return null;
  const names = [...seen].sort();
  return (
    <p>
      Cross-repo figures below also cover {names.length}{" "}
      {names.length === 1 ? "repository" : "repositories"} the workspace config no longer
      registers ({names.join(", ")}). Re-run a workspace sync to bring the two back into
      agreement.
    </p>
  );
}
