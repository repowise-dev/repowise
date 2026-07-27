import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { OverviewSection, SectionLink } from "@repowise-dev/ui/overview";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import {
  DecisionConflicts,
  GovernedFiles,
  summarizeGovernance,
} from "@repowise-dev/ui/decisions";
import {
  getDecision,
  getDecisionCounts,
  getDecisionGraph,
  listDecisions,
} from "@/lib/api/decisions";
import { ApiClientError } from "@/lib/api/client";
import { DecisionsTableWrapper } from "@/components/decisions/decisions-table-wrapper";

export const revalidate = 30;
export const metadata: Metadata = { title: "Decisions" };

/** Rows the table holds at once. The server pages; this is one window. */
const PAGE_SIZE = 50;

interface Props {
  params: Promise<{ id: string }>;
}

/**
 * Architectural decisions.
 *
 * The page used to open with a title and go straight into a filter bar, then
 * close with a React Flow canvas of the decision graph. Neither said the thing
 * the data says loudest: most records here are unconfirmed *proposals* mined by
 * the indexer, and only a handful are confirmed. That makes this a triage
 * queue, not an archive, and the lede now says so.
 *
 * Every figure comes from `/decisions/counts`, a grouped COUNT. An earlier cut
 * counted the rows it had fetched and printed "97 of 100" on a repository
 * holding several hundred — a count nobody measured, on the surface whose whole
 * job is to be trusted.
 *
 * The canvas is gone. See `decision-governance.tsx` for what its payload
 * actually contained and why two lists beat it.
 */
export default async function DecisionsPage({ params }: Props) {
  const { id: repoId } = await params;

  let decisions;
  try {
    decisions = await listDecisions(repoId, {
      include_proposed: true,
      limit: PAGE_SIZE,
      offset: 0,
    });
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) {
      notFound();
    }
    // Re-throw so the nearest error.tsx boundary can surface a retry UI
    throw err;
  }

  // Counts degrade rather than 404 the page. `/decisions/counts` is newer than
  // the rest of this route, so a frontend running ahead of its backend gets a
  // 404 here — and folding that into the repo-missing branch above turned a
  // missing *aggregate* into "Page not found" for a repository that exists.
  const counts = await getDecisionCounts(repoId, {
    include_proposed: true,
  }).catch(() => undefined);

  // Aggregated here, not in the browser: the graph payload runs to thousands of
  // code edges and only a dozen rows of it survive to the page.
  const graph = await getDecisionGraph(repoId).catch(() => undefined);

  // Conflict endpoints routinely sit outside the graph's own node payload — on
  // a live index every one of them did — so titles come from the rows we
  // already have, and anything still unnamed is fetched by id. Conflicts are
  // rare, so this stays a handful of requests.
  const titles = new Map(decisions.map((d) => [d.id, d.title]));
  const unresolved = [
    ...new Set(
      summarizeGovernance(graph, { titles })
        .conflicts.flatMap((c) => [
          ...(c.aTitle ? [] : [c.aId]),
          ...(c.bTitle ? [] : [c.bId]),
        ]),
    ),
  ];
  if (unresolved.length > 0) {
    const fetched = await Promise.all(
      unresolved.map((id) =>
        getDecision(repoId, id).then(
          (d) => [id, d.title] as const,
          () => null,
        ),
      ),
    );
    for (const row of fetched) if (row) titles.set(row[0], row[1]);
  }

  const { conflicts, governedFiles, governedFileTotal } = summarizeGovernance(
    graph,
    { topFiles: 12, titles },
  );
  // A pair still missing a title is dropped rather than shown as a hash.
  const namedConflicts = conflicts.filter((c) => c.aTitle && c.bTitle);

  // With counts we can state a measured total; without them we report only
  // what this page actually loaded, and say so. Never a number nobody counted.
  const total = counts?.total;
  const proposed =
    counts?.proposed ?? decisions.filter((d) => d.status === "proposed").length;
  const active =
    counts?.active ?? decisions.filter((d) => d.status === "active").length;
  const queue = proposed > 0;
  const denominator =
    total !== undefined
      ? `of ${total.toLocaleString()} recorded`
      : `in the first ${decisions.length.toLocaleString()}`;

  return (
    <PageShell
      title="Architectural decisions"
      description="Why the codebase is built the way it is — constraints, tradeoffs, and the alternatives that were rejected."
    >
      <PageLede
        label={queue ? "Awaiting review" : "Active decisions"}
        value={(queue ? proposed : active).toLocaleString()}
        unit={denominator}
        layout="beside"
      >
        {queue ? (
          <>
            <p>
              {proposed.toLocaleString()}{" "}
              {total !== undefined
                ? `of ${total.toLocaleString()} records are`
                : `of the first ${decisions.length.toLocaleString()} records are`}{" "}
              proposals mined from commits, comments and docs that nobody has
              confirmed yet, against {active.toLocaleString()} marked active.
              Until one is confirmed it is a guess about your codebase, not a
              rule for it — so this page is a queue before it is an archive.
            </p>
            <p>
              Confirming takes a click and changes no code. It marks the record
              as something your team stands behind, which is what makes it worth
              quoting back to an agent later.
            </p>
          </>
        ) : (
          <p>
            {active.toLocaleString()}{" "}
            {total !== undefined ? `of ${total.toLocaleString()} ` : ""}
            decisions are confirmed as current, with nothing waiting on review.
            New proposals appear here as the indexer mines them from commits,
            comments and docs.
          </p>
        )}
      </PageLede>

      {namedConflicts.length > 0 && (
        <OverviewSection
          title="Conflicts"
          description={`${namedConflicts.length} pair${
            namedConflicts.length === 1 ? "" : "s"
          } of decisions appear to contradict each other. Confirming one and deprecating the other resolves the pair.`}
        >
          <DecisionConflicts
            conflicts={namedConflicts}
            decisionHref={(id) => `/repos/${repoId}/decisions/${id}`}
            LinkComponent={Link}
          />
        </OverviewSection>
      )}

      <OverviewSection
        title="All decisions"
        description="Confirmed rules first, then the proposals most likely to be real. Filter by status to work the queue, or by source to see where a record came from."
      >
        <DecisionsTableWrapper
          repoId={repoId}
          initialData={decisions}
          {...(total !== undefined ? { initialTotal: total } : {})}
          pageSize={PAGE_SIZE}
        />
      </OverviewSection>

      {governedFiles.length > 0 && (
        <OverviewSection
          title="Most governed files"
          description={`Of ${governedFileTotal.toLocaleString()} files carrying at least one decision, these have the most. Worth reading before you change one.`}
          action={
            <SectionLink href={`/repos/${repoId}/files`} LinkComponent={Link}>
              All files
            </SectionLink>
          }
        >
          <GovernedFiles
            files={governedFiles}
            fileHref={(path) =>
              `/repos/${repoId}/files?path=${encodeURIComponent(path)}`
            }
            LinkComponent={Link}
          />
        </OverviewSection>
      )}
    </PageShell>
  );
}
