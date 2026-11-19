import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { CheckoutFactsDisclosure } from "@repowise-dev/ui/decisions";
import {
  getDecisionCounts,
  getDecisionLaneCounts,
  listDecisions,
} from "@/lib/api/decisions";
import { listEpisodes } from "@/lib/api/episodes";
import { ApiClientError } from "@/lib/api/client";
import { DecisionAgentPanel } from "@/components/decisions/add-decision-button";
import { DecisionsBoard } from "@/components/decisions/decision-review-wrapper";
import { DecisionsLede } from "@/components/decisions/decisions-lede";
import { getTranslations } from "next-intl/server";

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

  // The lede's figure, seeded here for the first paint. The lede and the tab
  // badges then read it from the same client key, so a review moves both.
  // `/decisions/counts` groups the status column, the projection kept for
  // readers that predate the acceptance split, so it is only the fallback for
  // a backend that 404s here. The ungoverned-hotspots read lives on the client
  // for the same reason: under the decisions prefix a review revalidates it.
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

  const empty = (lanes?.total ?? counts?.total) === 0;

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
    >
      <DecisionsLede
        repoId={repoId}
        initialLanes={lanes}
        statusFallback={{
          total: counts?.total,
          proposed:
            counts?.proposed ?? decisions.filter((d) => d.status === "proposed").length,
          active: counts?.active ?? decisions.filter((d) => d.status === "active").length,
          loaded: decisions.length,
        }}
      />

      {/* The one handoff: the agent maintains the record, the person audits
          it. Visible on an empty store too, where the server hands the add
          pass instead, which is exactly when somebody needs the first one. */}
      <DecisionAgentPanel repoId={repoId} />

      <DecisionsBoard
        repoId={repoId}
        pageSize={PAGE_SIZE}
        initialLaneCounts={lanes}
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
