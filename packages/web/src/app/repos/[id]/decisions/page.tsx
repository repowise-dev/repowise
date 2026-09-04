import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { CheckoutFacts } from "@repowise-dev/ui/decisions";
import {
  getDecisionCounts,
  getDecisionLaneCounts,
  listDecisions,
} from "@/lib/api/decisions";
import { listEpisodes } from "@/lib/api/episodes";
import { ApiClientError } from "@/lib/api/client";
import { AddDecisionButton } from "@/components/decisions/add-decision-button";
import { DecisionReviewWrapper } from "@/components/decisions/decision-review-wrapper";

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

  // The lede's figure. `/decisions/counts` groups the status column, which is
  // the projection kept in step for readers that predate the acceptance split,
  // so its `active` counts records nobody accepted. The number this page leads
  // with is what the repository actually stands behind, which is the lane
  // count. Degrades the same way: a frontend ahead of its backend 404s here
  // and falls back to the projection rather than losing the page.
  const lanes = await getDecisionLaneCounts(repoId).catch(() => undefined);

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
  const total = lanes?.total ?? counts?.total;
  const proposed =
    lanes?.candidates ??
    counts?.proposed ??
    decisions.filter((d) => d.status === "proposed").length;
  // Everything with an acceptance row, which is what "accepted" means. Not
  // `governing`: that is `active + needs_review` and leaves the accepted but
  // unscoped records out, so the lede and the Uncheckable tab under it would
  // not add up. The status fallback counts the projection instead, and it can
  // include records nobody accepted, so it is labelled differently below.
  const accepted =
    lanes !== undefined
      ? lanes.active + lanes.needs_review + lanes.uncheckable
      : (counts?.active ??
        decisions.filter((d) => d.status === "active").length);
  const measuredByLane = lanes !== undefined;
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
          what the repository actually stands behind. The accepted count leads
          now; the queue keeps its sentence, because a few hundred unreviewed
          candidates are worth knowing about, just not worth 52px.

          It counts acceptance rows, not the status column, so it is every
          record somebody accepted: the ones that govern plus the ones that
          were accepted and then drifted or turned out to name nothing. Those
          are separate lanes below and the four numbers add up.

          The count is not de-duplicated. A live index carries near-identical
          pairs, two spellings of one fix landing as two accepted records, and
          collapsing them here would need a title-similarity threshold tuned on
          one repository sitting in a page component, where a wrong merge hides
          a record a human accepted. That belongs to supersession, which is
          structural and not yet built. Until then the number is honest about
          what it counts and the pairs stay visible in their lane, where they
          can be superseded. */}
      <PageLede
        label={measuredByLane ? "Accepted" : "Confirmed"}
        value={accepted.toLocaleString()}
        unit={denominator}
        layout="beside"
      >
        {empty ? (
          <p>
            Decisions land here as the indexer mines them from pull requests,
            commit messages, code comments and your own sessions, and as you
            record them yourself — below, or with{" "}
            <code>repowise decision add</code>. Each one keeps the quote it was
            drawn from and the files it governs.
          </p>
        ) : accepted === 0 ? (
          // A repository with records and no accepted one is not an empty
          // repository, and it used to get the copy for one: "decisions land
          // here as the indexer mines them" on a store already holding five
          // hundred of them. It is also what a store looks like the first time
          // it is read after the acceptance split, when everything the status
          // column called active turns out to be something nobody accepted, so
          // this is the state most likely to be read by somebody surprised.
          <>
            <p>
              Nothing here governs yet. A record governs only once somebody
              accepts it, naming the files it covers and why — mining it,
              seeing it recur, and scoring it confident all stop short of that.
            </p>
            <p>
              {proposed.toLocaleString()} candidate
              {proposed === 1 ? " is" : "s are"} waiting below, and{" "}
              {proposed === 1 ? "it is" : "they are"} all still here: nothing
              was deleted and every id still resolves. If this page used to
              show confirmed decisions, those are the records now in
              Candidates. Accepting one takes a click and changes no code.
            </p>
          </>
        ) : (
          <>
            <p>
              {accepted.toLocaleString()} record
              {accepted === 1 ? " is" : "s are"} accepted: somebody read{" "}
              {accepted === 1 ? "it" : "them"} and marked{" "}
              {accepted === 1 ? "it" : "them"} as something the team stands
              behind, which is what makes {accepted === 1 ? "it" : "them"} worth
              quoting back to an agent later.
            </p>
            {proposed > 0 && (
              <p>
                {proposed.toLocaleString()} more are candidates the indexer
                mined and nobody has accepted. Until one is accepted it is a
                guess about your codebase rather than a rule for it, and no
                agent is ever given one. Open the Candidates lane below to work
                through them; accepting or dismissing one takes a click and
                changes no code.
              </p>
            )}
          </>
        )}
      </PageLede>

      {/* Visible in both states on purpose: the table section below is not
          rendered at all on an empty repo, which is exactly when somebody
          needs to record the first one. */}
      <div className="flex justify-end">
        <AddDecisionButton repoId={repoId} />
      </div>

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
          title="Review"
          description="Split by what each record's acceptance amounts to, not by a status word. Every record is in exactly one lane and the counts add up: what governs, what nobody has reviewed, what has drifted from the code it names, what names nothing at all, and what has been retired."
        >
          <DecisionReviewWrapper repoId={repoId} pageSize={PAGE_SIZE} />
        </OverviewSection>
      )}


    </PageShell>
  );
}
