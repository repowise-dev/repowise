import type { RepoSummaryRow } from "@repowise-dev/types/repos";
import en from "../../messages/en.json";

/**
 * Ordering and prose for the multi-repo dashboard.
 *
 * Kept out of the page so both are testable: a page file can only export the
 * handful of names the App Router recognises, and these two carry the
 * decisions worth pinning — which repo the reader is steered to first, and
 * what the page claims when nothing is wrong.
 *
 * The prose is translated. `t` is an optional parameter typed as the minimal
 * translator next-intl's `t` satisfies; calling without it falls back to the
 * English catalog, which keeps every caller that only wants the English
 * sentence — and the unit tests with it — working unchanged.
 */

export type AttentionTranslator = (
  key: string,
  values?: Record<string, string | number>,
) => string;

/** `{name}`-style substitution. Deliberately tiny: these are plain sentences,
 *  not ICU plurals, and keeping it local means the English fallback below has
 *  no dependency on the i18n runtime. */
function interpolate(
  template: string,
  values?: Record<string, string | number>,
): string {
  if (!values) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in values ? String(values[name]) : match,
  );
}

const englishAttention: AttentionTranslator = (key, values) =>
  interpolate(
    (en.attention as Record<string, string>)[key] ?? key,
    values,
  );

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
export function attentionSentence(
  repos: RepoSummaryRow[],
  t: AttentionTranslator = englishAttention,
): string {
  const unindexed = repos.filter((r) => r.status !== "indexed");
  const behind = repos.filter((r) => r.index_behind === true);
  const parts: string[] = [];

  if (unindexed.length > 0) {
    parts.push(
      unindexed.length === 1
        ? t("unindexedOne", { name: unindexed[0].name })
        : t("unindexedMany", { count: unindexed.length }),
    );
  }

  if (behind.length > 0) {
    parts.push(
      behind.length === 1
        ? t("behindOne", { name: behind[0].name })
        : t("behindMany", { count: behind.length }),
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
      t("lowestScore", {
        score: (worst.average_health as number).toFixed(1),
        name: worst.name,
      }),
    );
  }

  if (parts.length === 0) {
    return t("nothingAnalysed");
  }
  if (unindexed.length === 0 && behind.length === 0) {
    return t("allCurrent", { parts: parts.join(" ") });
  }
  return parts.join(" ");
}
