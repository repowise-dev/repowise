import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { getOverviewSummary } from "@/lib/api/overview";
import { getProviders } from "@/lib/api/providers";
import { getCommitsPage } from "@/lib/api/git";
import { FirstIndexExperience } from "@/components/repos/first-index-experience";
import { QuickActionsWrapper } from "@/components/dashboard/quick-actions-wrapper";
import { AskAnythingRow } from "@/components/overview/ask-anything-row";
import {
  OverviewBody,
  RepoIdentityHeader,
  type OverviewRoutes,
  type RepoIdentityMeta,
} from "@repowise-dev/ui/overview";
import {
  formatDateTime,
  formatLOC,
  formatRelativeTime,
} from "@repowise-dev/ui/lib/format";

export const metadata: Metadata = { title: "Overview" };

interface Props {
  params: Promise<{ id: string }>;
}

async function safeFetch<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

/** Rows shown in the activity section. Fetched server-side so the list is in
 *  the initial HTML instead of waterfalling in after paint. */
const COMMIT_LIMIT = 6;

/**
 * The local dashboard's repo Overview.
 *
 * Everything this file does is host-specific: fetch on the server, resolve the
 * `/repos/{id}` route shape, and supply the two panels only the local app has
 * (reindex actions in the header, the ask box). The page itself — what it
 * says, in what order — is `OverviewBody` in the ui package, shared with
 * hosted so a new signal lands on both surfaces at once instead of being added
 * twice and drifting.
 */
export default async function OverviewPage({ params }: Props) {
  const { id } = await params;

  // Every fetch the page needs, in one wave, on the server. Nothing below
  // renders client-side: this is a static read, so a hydration boundary would
  // only buy a slower first paint and worse indexability. The commits call
  // replaces a client-side SWR fetch that used to waterfall in after paint.
  const [summary, providers, commitsPage] = await Promise.all([
    safeFetch(() => getOverviewSummary(id)),
    safeFetch(() => getProviders()),
    safeFetch(() => getCommitsPage(id, { sort: "date", limit: COMMIT_LIMIT })),
  ]);
  if (!summary) notFound();

  const { repo, stats, health, sync } = summary;
  const isFresh = stats.file_count === 0;
  const base = `/repos/${id}`;
  const totalNloc = stats.total_nloc ?? null;
  const lastActivityAt = sync.last_sync_at ?? sync.last_resync_at ?? health.last_indexed_at;

  // ── Identity ───────────────────────────────────────────────────────────
  const meta: RepoIdentityMeta[] = [];
  const primaryLanguage = summary.languages[0]?.language;
  if (primaryLanguage) meta.push({ label: primaryLanguage, dot: true });
  meta.push({ label: repo.default_branch, mono: true });
  if (repo.head_commit) meta.push({ label: repo.head_commit.slice(0, 7), mono: true });
  if (totalNloc != null) {
    meta.push({ label: `${formatLOC(totalNloc)} lines`, mono: true });
  }
  if (lastActivityAt) {
    meta.push({
      label: `synced ${formatRelativeTime(lastActivityAt)}`,
      title: formatDateTime(lastActivityAt),
    });
  }

  const header = (
    <RepoIdentityHeader
      name={repo.name}
      owner={repo.owner}
      description={repo.local_path}
      remoteUrl={repo.remote_url}
      meta={isFresh ? [] : meta}
      actions={
        isFresh ? null : (
          <QuickActionsWrapper
            variant="header"
            repoId={id}
            repoName={repo.name}
            pageCount={sync.page_count || stats.file_count}
            modelName={providers?.active.model ?? sync.last_sync_model ?? ""}
            lastSyncAt={sync.last_sync_at}
            lastResyncAt={sync.last_resync_at}
          />
        )
      }
    />
  );

  if (isFresh) {
    return (
      <div className="mx-auto flex w-full max-w-[1280px] flex-col gap-8 p-[var(--page-pad)]">
        {header}
        <FirstIndexExperience repoId={id} repoName={repo.name} />
      </div>
    );
  }

  // Coupling and the language graph take the shared defaults; `chat` is absent
  // because this app answers that need with the ask box below instead of a
  // route, and `settings`/`costs` exist here and are what the index-size and
  // savings reads link to.
  const routes: OverviewRoutes = {
    base,
    settings: `${base}/settings`,
    costs: `${base}/costs`,
  };

  return (
    <OverviewBody
      summary={summary}
      routes={routes}
      commits={commitsPage?.items ?? []}
      totalNloc={totalNloc}
      LinkComponent={Link}
      slots={{ header, ask: <AskAnythingRow repoId={id} /> }}
    />
  );
}
