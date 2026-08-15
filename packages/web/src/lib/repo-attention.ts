import type { RepoSummaryRow } from "@repowise-dev/types/repos";

/**
 * Ordering and prose for the multi-repo dashboard.
 *
 * Kept out of the page so both are testable: a page file can only export the
 * handful of names the App Router recognises, and these two carry the
 * decisions worth pinning — which repo the reader is steered to first, and
 * what the page claims when nothing is wrong.
 */

/** Score to sort a never-analysed repo by. Mid-band on purpose: absent is not
 *  zero, and sorting it as zero would park every unanalysed repo above the
 *  genuinely unhealthy one. */
const UNSCORED_RANK = 6;

/** Never indexed first, then behind the checkout, then worst health, then by
 *  name so the order is stable between renders. */
export function byAttention(a: RepoSummaryRow, b: RepoSummaryRow): number {
  const rank = (r: RepoSummaryRow) => (r.status !== "indexed" ? 0 : r.index_behind === true ? 1 : 2);
  const byRank = rank(a) - rank(b);
  if (byRank !== 0) return byRank;

  const score = (r: RepoSummaryRow) => r.average_health ?? UNSCORED_RANK;
  const byScore = score(a) - score(b);
  if (byScore !== 0) return byScore;

  return a.name.localeCompare(b.name);
}

/**
 * The sentence under the lede figure, which is the part that makes the count
 * mean something.
 *
 * Reports the worst thing that is true, and says so plainly when nothing is —
 * an empty state that says "nothing is wrong" is worth more than one that says
 * nothing at all.
 */
export function attentionSentence(repos: RepoSummaryRow[]): string {
  const unindexed = repos.filter((r) => r.status !== "indexed");
  const behind = repos.filter((r) => r.index_behind === true);
  const parts: string[] = [];

  if (unindexed.length > 0) {
    parts.push(
      unindexed.length === 1
        ? `${unindexed[0].name} has not been indexed yet.`
        : `${unindexed.length} repositories have not been indexed yet.`,
    );
  }

  if (behind.length > 0) {
    parts.push(
      behind.length === 1
        ? `${behind[0].name} is behind its working tree — run repowise update in it to catch up.`
        : `${behind.length} are behind their working trees — run repowise update in them to catch up.`,
    );
  }

  // Only repos that have actually been analysed can hold the "lowest score".
  // Treating a null as a 0 here would name an unanalysed repo as the worst one
  // on the machine.
  const scored = repos.filter((r) => r.average_health !== null);
  if (scored.length > 0) {
    const worst = scored.reduce((a, b) =>
      (a.average_health as number) <= (b.average_health as number) ? a : b,
    );
    parts.push(
      `Lowest health score is ${(worst.average_health as number).toFixed(1)} out of 10, in ${worst.name}.`,
    );
  }

  if (parts.length === 0) {
    return "None of them has been analysed yet, so there are no health scores to compare.";
  }
  if (unindexed.length === 0 && behind.length === 0) {
    return `Every index is current with its checkout. ${parts.join(" ")}`;
  }
  return parts.join(" ");
}
