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
import { listDecisions, getDecisionGraph } from "@/lib/api/decisions";
import { ApiClientError } from "@/lib/api/client";
import { DecisionsTableWrapper } from "@/components/decisions/decisions-table-wrapper";

export const revalidate = 30;
export const metadata: Metadata = { title: "Decisions" };

interface Props {
  params: Promise<{ id: string }>;
}

/**
 * Architectural decisions.
 *
 * The page used to open with a title and go straight into a filter bar, then
 * close with a React Flow canvas of the decision graph. Neither said the thing
 * the data says loudest: on a real index 185 of 200 records are unconfirmed
 * *proposals* and four are active. That makes this a triage queue, not an
 * archive, and the lede now says so.
 *
 * The canvas is gone. See `decision-governance.tsx` for what its payload
 * actually contained and why two lists beat it.
 */
export default async function DecisionsPage({ params }: Props) {
  const { id: repoId } = await params;

  let decisions;
  try {
    decisions = await listDecisions(repoId, { include_proposed: true, limit: 100 });
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) {
      notFound();
    }
    // Re-throw so the nearest error.tsx boundary can surface a retry UI
    throw err;
  }

  // Aggregated here, not in the browser: the graph payload runs to thousands of
  // code edges and only ~20 rows of it survive to the page.
  const graph = await getDecisionGraph(repoId).catch(() => undefined);
  const { conflicts, governedFiles, governedFileTotal } = summarizeGovernance(graph, {
    topFiles: 12,
  });

  const proposed = decisions.filter((d) => d.status === "proposed").length;
  const active = decisions.filter((d) => d.status === "active").length;
  const total = decisions.length;

  return (
    <PageShell
      title="Architectural decisions"
      description="Why the codebase is built the way it is — constraints, tradeoffs, and the alternatives that were rejected."
    >
      <PageLede
        label={proposed > 0 ? "Awaiting review" : "Active decisions"}
        value={(proposed > 0 ? proposed : active).toLocaleString()}
        unit={`of ${total.toLocaleString()} recorded`}
        layout="beside"
      >
        {proposed > 0 ? (
          <>
            <p>
              {proposed} of {total} recorded decisions are proposals mined from
              commits, comments and docs that nobody has confirmed yet, against{" "}
              {active} marked active. Until one is confirmed it is a guess about
              your codebase, not a rule for it — so this page is a queue before
              it is an archive.
            </p>
            <p>
              Confirming takes a click and changes no code. It marks the record
              as something your team stands behind, which is what makes it worth
              quoting back to an agent later.
            </p>
          </>
        ) : (
          <p>
            {active} of {total} decisions are confirmed as current, with nothing
            waiting on review. New proposals appear here as the indexer mines
            them from commits, comments and docs.
          </p>
        )}
      </PageLede>

      {conflicts.length > 0 && (
        <OverviewSection
          title="Conflicts"
          description={`${conflicts.length} pair${
            conflicts.length === 1 ? "" : "s"
          } of decisions appear to contradict each other. Confirming one and deprecating the other resolves the pair.`}
        >
          <DecisionConflicts
            conflicts={conflicts}
            decisionHref={(id) => `/repos/${repoId}/decisions/${id}`}
            LinkComponent={Link}
          />
        </OverviewSection>
      )}

      <OverviewSection
        title="All decisions"
        description="Filter by status to work the queue, or by source to see where a record came from."
      >
        <DecisionsTableWrapper repoId={repoId} initialData={decisions} />
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
