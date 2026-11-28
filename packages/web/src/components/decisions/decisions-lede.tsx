"use client";

import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import type { DecisionLaneCounts } from "@repowise-dev/types/decisions";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { useTranslations } from "next-intl";
import { useDecisionLaneCounts } from "./decision-review-wrapper";

interface Props {
  repoId: string;
  /** The server's lane-count read, so the first paint has the figure. */
  initialLanes?: DecisionLaneCounts | undefined;
  /**
   * The status-column projection, for a backend without `/lane-counts`: its
   * total, proposed and active, each counted from the loaded rows when the
   * aggregate was missing too, and how many rows were loaded.
   */
  statusFallback: {
    total?: number | undefined;
    proposed: number;
    active: number;
    loaded: number;
  };
}

/**
 * The page's headline figure.
 *
 * A client component for one reason: it reads the same SWR lane counts as the
 * In force tab badge, so a review that moves a record revalidates both, and
 * the lede never disagrees with the tab under it until the next reload.
 *
 * The headline used to be the size of the review queue, so the largest number
 * on the page pointed at the work nobody had done rather than at what the
 * repository actually stands behind. What is in force leads now; the
 * background keeps its sentence.
 *
 * It counts acceptance rows, not the status column, so it is every record
 * somebody accepted and nobody retired. The count is not de-duplicated: near
 * identical accepted pairs belong to supersession, which is structural and not
 * yet built, and a title-similarity merge here could hide a record a human
 * accepted.
 */
export function DecisionsLede({ repoId, initialLanes, statusFallback }: Props) {
  const t = useTranslations("views.decisions");
  const { data: lanes } = useDecisionLaneCounts(repoId, initialLanes);

  // With counts we can state a measured total; without them we report only
  // what this page actually loaded, and say so. Never a number nobody counted.
  const total = lanes?.total ?? statusFallback.total;
  const proposed = lanes?.candidates ?? statusFallback.proposed;
  // Everything with an acceptance row, which is what "accepted" means. Not
  // `governing`: that is `active + needs_review` and leaves the accepted but
  // unscoped records out. The status fallback counts the projection instead,
  // and it can include records nobody accepted, so it is labelled differently.
  const measuredByLane = lanes !== undefined;
  const accepted = measuredByLane
    ? lanes.active + lanes.needs_review + lanes.uncheckable
    : statusFallback.active;
  // `in_force` is absent on a backend that predates it; the acceptance sum is
  // the same set there.
  const inForce = lanes?.in_force ?? accepted;
  const empty = total === 0;
  const denominator =
    total !== undefined
      ? `of ${formatNumber(total)} recorded`
      : `in the first ${formatNumber(statusFallback.loaded)}`;

  return (
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
        // repository, and it is what a store looks like the first time it is
        // read after the acceptance split, so it gets its own copy.
        <>
          <p>{t("ledeNoGovernance")}</p>
          <p>{t("ledeCandidates", { count: proposed })}</p>
        </>
      ) : (
        <>
          <p>{t("ledeAcceptedRecords", { count: inForce })}</p>
          {proposed > 0 && <p>{t("ledeAcceptedMore", { count: proposed })}</p>}
        </>
      )}
    </PageLede>
  );
}
