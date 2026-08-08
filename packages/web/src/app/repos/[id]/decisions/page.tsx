import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { CheckoutFacts } from "@repowise-dev/ui/decisions";
import { getDecisionCounts, listDecisions } from "@/lib/api/decisions";
import { listEpisodes } from "@/lib/api/episodes";
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
 * the indexer, and only a handful are confirmed.
 *
 * Every figure comes from `/decisions/counts`, a grouped COUNT. An earlier cut
 * counted the rows it had fetched and printed "97 of 100" on a repository
 * holding several hundred — a count nobody measured, on the surface whose whole
 * job is to be trusted.
 *
 * Two sections and a fetch left in the same pass, and the reason for each is
 * the same distribution argument that deleted the canvas before them.
 *
 * **Conflicts** rendered from `decision_edges`, which holds zero rows, and not just on
 * a fresh repository, on every repository, because the detector that wrote
 * them was disabled for retiring 74 records on loose semantic matches and its
 * structural replacement is not built. A section that cannot render is not a
 * section.
 *
 * **Most governed files** ranked files by how many records name them, under a
 * heading that reads like settled governance. On a live index 81% of those
 * links belong to *proposed* records, so it ranked files by unconfirmed
 * guesses and told the reader they were "worth reading before you change one".
 * Scoping it to confirmed records does not rescue it: the top file falls from
 * 42 links to 4 and the top twelve run 4,3,3,3,3,3,3,2,2,2,2,2, which is a
 * proportion bar over noise. The honest version of that number is per-file,
 * and the file page already carries it.
 *
 * Both components stay exported, because the hosted decisions view mounts them, and
 * both fed on `getDecisionGraph`, whose payload is roughly 261 KB of code
 * edges for twelve rows. Dropping it takes the page's whole graph fetch, the
 * follow-up per-conflict title lookups, and a swallowed failure with it: that
 * call intermittently 500s and the page caught it silently, so two sections
 * disappeared with nothing to say they had.
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

  // Facts about the checkout, which is the structural tier of the episode
  // store. Five rows on this repository and none of them need history, a key
  // or a transcript, so this is the one section that says something on a
  // repository indexed an hour ago.
  //
  // The git tier is deliberately not fetched. It is 289 rows of one fix commit
  // each against 301 `fix:`-typed commits in the same window, and Commits is
  // two items up the nav, so a list of it here is that page with a filter on
  // it. A failure and a cold start are different: `available: false` is a 200
  // meaning this repository never derived episodes and gets copy saying what
  // will fill the section, whereas a throw means we do not know and the
  // section does not render at all.
  const facts = await listEpisodes(repoId, {
    tier: "structural",
    limit: 50,
  }).catch(() => undefined);

  // With counts we can state a measured total; without them we report only
  // what this page actually loaded, and say so. Never a number nobody counted.
  const total = counts?.total;
  const proposed =
    counts?.proposed ?? decisions.filter((d) => d.status === "proposed").length;
  const active =
    counts?.active ?? decisions.filter((d) => d.status === "active").length;
  const empty = total === 0;
  const denominator =
    total !== undefined
      ? `of ${total.toLocaleString()} recorded`
      : `in the first ${decisions.length.toLocaleString()}`;

  return (
    <PageShell
      title="Decisions"
      description="What this codebase has settled on and what is true of the checkout you are standing in: the constraints, the tradeoffs, and the facts that catch people out."
    >
      {/* The headline used to be the size of the review queue, so the largest
          number on the page pointed at the work nobody had done rather than at
          what the repository actually stands behind. The confirmed count leads
          now; the queue keeps its sentence, because 87 unconfirmed proposals
          are worth knowing about, just not worth 52px.

          The count is the measured `active` total and is not de-duplicated. A
          live index carries near-identical pairs, two spellings of one fix
          landing as two confirmed records, and collapsing them here would need
          a title-similarity threshold tuned on one repository sitting in a
          page component, where a wrong merge hides a record a human confirmed.
          That belongs to supersession, which is structural and not yet built.
          Until then the number is honest about what it counts and the pairs
          stay visible in the table, where they can be deprecated. */}
      <PageLede
        label="Confirmed"
        value={active.toLocaleString()}
        unit={denominator}
        layout="beside"
      >
        {empty || active === 0 ? (
          <>
            <p>
              Decisions land here as the indexer mines them from pull requests,
              commit messages, code comments and your own sessions, and as you
              record them with <code>repowise decision add</code>. Each one
              keeps the quote it was drawn from and the files it governs.
            </p>
            {proposed > 0 && (
              <p>
                {proposed.toLocaleString()} proposal
                {proposed === 1 ? " is" : "s are"} waiting below. Confirming one
                takes a click and changes no code; it marks the record as
                something the team stands behind, which is what makes it worth
                quoting back to an agent later.
              </p>
            )}
          </>
        ) : (
          <>
            <p>
              {active.toLocaleString()} record
              {active === 1 ? " is" : "s are"} confirmed: somebody read{" "}
              {active === 1 ? "it" : "them"} and marked{" "}
              {active === 1 ? "it" : "them"} as something the team stands
              behind, which is what makes {active === 1 ? "it" : "them"} worth
              quoting back to an agent later.
            </p>
            {proposed > 0 && (
              <p>
                {proposed.toLocaleString()} more are proposals the indexer mined
                and nobody has confirmed yet. Until one is confirmed it is a
                guess about your codebase rather than a rule for it. Filter by
                status below to work through them; confirming or dismissing one
                takes a click and changes no code.
              </p>
            )}
          </>
        )}
      </PageLede>

      {facts && (
        <OverviewSection
          title="About this checkout"
          description="Facts about the working tree rather than claims anybody made about it, derived at index time with no model call and no history. These are the ones that trip up a newcomer, human or agent."
        >
          <CheckoutFacts
            facts={facts.episodes}
            available={facts.available}
            total={facts.total}
          />
        </OverviewSection>
      )}

      {!empty && (
        <OverviewSection
          title="All decisions"
          description="Confirmed records first, then the proposals most likely to be real. Filter by status to work the queue, or by source to see where a record came from. The last column is a count, not a score: how many of the files a record names have been committed to since it was recorded, or are no longer tracked."
        >
          <DecisionsTableWrapper
            repoId={repoId}
            initialData={decisions}
            {...(total !== undefined ? { initialTotal: total } : {})}
            pageSize={PAGE_SIZE}
          />
        </OverviewSection>
      )}
    </PageShell>
  );
}
