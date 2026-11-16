import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { CheckoutFactsDisclosure } from "@repowise-dev/ui/decisions";
import {
  getDecisionCounts,
  getDecisionHealth,
  getDecisionLaneCounts,
  listDecisions,
} from "@/lib/api/decisions";
import { listEpisodes } from "@/lib/api/episodes";
import { ApiClientError } from "@/lib/api/client";
import { DecisionAgentPanel } from "@/components/decisions/add-decision-button";
import { DecisionsBoard } from "@/components/decisions/decision-review-wrapper";
import { getTranslations } from "next-intl/server";
import { formatNumber } from "@repowise-dev/ui/lib/format";

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
  const t = await getTranslations("views.decisions");

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

  // The digest's ungoverned-hotspots row. The rollup scans every record and
  // ships the stale and background lists too, which this page does not show;
  // it is read once per render (cached for `revalidate`), and a failure only
  // drops the row. Upgrade path: a counts-only health read on the server.
  const health = await getDecisionHealth(repoId).catch(() => undefined);

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
  // The lede's figure is what governs, the same number as the In force tab.
  // `in_force` is absent on a backend that predates it; the acceptance sum is
  // the same set there.
  const inForce = lanes?.in_force ?? accepted;
  const empty = total === 0;
  const denominator =
    total !== undefined
      ? `of ${formatNumber(total)} recorded`
      : `in the first ${formatNumber(decisions.length)}`;

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
    >
      {/* The headline used to be the size of the review queue, so the largest
          number on the page pointed at the work nobody had done rather than at
          what the repository actually stands behind. What is in force leads
          now; the background keeps its sentence, because a few hundred
          unreviewed records are worth knowing about, just not worth 52px.

          It counts acceptance rows, not the status column, so it is every
          record somebody accepted and nobody retired: the ones that govern
          plus the ones that were accepted and then drifted or turned out to
          name nothing. It is the In force tab's own count.

          The count is not de-duplicated. A live index carries near-identical
          pairs, two spellings of one fix landing as two accepted records, and
          collapsing them here would need a title-similarity threshold tuned on
          one repository sitting in a page component, where a wrong merge hides
          a record a human accepted. That belongs to supersession, which is
          structural and not yet built. Until then the number is honest about
          what it counts and the pairs stay visible in their lane, where they
          can be superseded. */}
      <PageLede
        label={measuredByLane ? t("ledeAccepted") : t("ledeConfirmed")}
        value={formatNumber(inForce)}
        unit={denominator}
        layout="beside"
      >
        {empty ? (
          <p>
            {t.rich("ledeEmpty", {
              code: (chunks) => <code>{chunks}</code>,
            })}
          </p>
        ) : inForce === 0 ? (
          // A repository with records and no accepted one is not an empty
          // repository, and it used to get the copy for one: "decisions land
          // here as the indexer mines them" on a store already holding five
          // hundred of them. It is also what a store looks like the first time
          // it is read after the acceptance split, when everything the status
          // column called active turns out to be something nobody accepted, so
          // this is the state most likely to be read by somebody surprised.
          <>
            <p>{t("ledeNoGovernance")}</p>
            <p>{t("ledeCandidates", { count: proposed })}</p>
          </>
        ) : (
          <>
            <p>{t("ledeAcceptedRecords", { count: inForce })}</p>
            {proposed > 0 && (
              <p>{t("ledeAcceptedMore", { count: proposed })}</p>
            )}
          </>
        )}
      </PageLede>

      {/* The one handoff: the agent maintains the record, the person audits
          it. Visible on an empty store too, where the server hands the add
          pass instead, which is exactly when somebody needs the first one. */}
      <DecisionAgentPanel repoId={repoId} />

      <DecisionsBoard
        repoId={repoId}
        pageSize={PAGE_SIZE}
        ungovernedHotspots={health?.ungoverned_hotspots}
        hasRecords={!empty}
      />

      {/* Closed by default, with every fact's heading on the toggle: the
          digest and the record above are what this page is visited for. */}
      {facts && (
        <CheckoutFactsDisclosure
          title={t("checkoutTitle")}
          description={t("checkoutDescription")}
          facts={facts.episodes}
          available={facts.available}
          total={facts.total}
        />
      )}
    </PageShell>
  );
}
