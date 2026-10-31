import { getTranslations } from "next-intl/server";
import type { OverviewBodyCopy } from "@repowise-dev/ui/overview";
import { attentionSourceLabel } from "@repowise-dev/ui/dashboard/attention-href";
import {
  formatRelativeTime,
  RELATIVE_TIME_WORDS,
  type RelativeTimeUnit,
  type RelativeTimeWords,
} from "@repowise-dev/ui/lib/format";

/** `overview.ago.*` as raw strings with their `{value}` placeholder, so a
 *  client component can word an age it measures with the viewer's clock. */
async function overviewAgoWords(): Promise<RelativeTimeWords> {
  const t = await getTranslations("overview.ago");
  const units = Object.keys(RELATIVE_TIME_WORDS) as RelativeTimeUnit[];
  return Object.fromEntries(units.map((u) => [u, t.raw(u) as string])) as RelativeTimeWords;
}

/** "2d ago" in the active locale, from `overview.ago.*`. */
export async function overviewRelativeTime(): Promise<(iso: string) => string> {
  const words = await overviewAgoWords();
  return (iso) => formatRelativeTime(iso, words);
}

/**
 * Overview body copy in the active locale, from `overview.*`. Built on the
 * server: `OverviewBody` and its children are server components here, so the
 * function-valued fields never cross a client boundary.
 */
export async function overviewBodyCopy(): Promise<OverviewBodyCopy> {
  const t = await getTranslations("overview");
  const tBand = await getTranslations("welcome.repo.band");
  const ago = await overviewAgoWords();
  return {
    exploreTitle: t("body.exploreTitle"),
    exploreMore: t("body.exploreMore"),
    changeLine: { since: (when) => t("changeLine.since", { when }), ago },
    healthLede: {
      label: t("healthLede.label"),
      unit: t("healthLede.unit"),
      band: (band) => tBand(band),
      reportLink: t("healthLede.reportLink"),
      empty: t("healthLede.empty"),
      hint: (counts) => (counts ? t("healthLede.hintHotspots", counts) : t("healthLede.hint")),
      hotspotReading: (figure, holdsUp) =>
        t.rich(holdsUp ? "healthLede.hotspotsHealthy" : "healthLede.hotspotsPayoff", {
          score: () => figure,
        }),
    },
    model: {
      changeFiles: (delta) => t("changeLine.files", { count: Math.abs(delta) }),
      changeCodeHealth: t("changeLine.codeHealth"),
      changeHotspotHealth: t("changeLine.hotspotHealth"),
      docsLabel: t("reads.docsLabel"),
      docsUnit: t("reads.docsUnit"),
      docsSplit: (prose, withoutProse) => t("reads.docsSplit", { prose, withoutProse }),
      docsModules: (modules) => t("reads.docsModules", { modules }),
      docsProseBar: t("reads.docsProseBar"),
      docsIndexBar: t("reads.docsIndexBar"),
      cardHealth: t("cards.health"),
      cardOwnership: t("cards.ownership"),
      cardArchitecture: t("cards.architecture"),
      cardDecisions: t("cards.decisions"),
      cardRefactoring: t("cards.refactoring"),
      cardTests: t("cards.tests"),
      healthScored: t("cards.healthScored"),
      healthHotspots: (hotspots, files) => t("cards.healthHotspots", { hotspots, files }),
      ownershipEmpty: t("cards.ownershipEmpty"),
      ownershipSole: (files) => t("cards.ownershipSole", { files }),
      architectureModules: (modules, entryPoints) =>
        t("cards.architectureModules", { modules, entryPoints }),
      decisionsEmpty: t("cards.decisionsEmpty"),
      decisionsRecorded: (accepted, candidates) =>
        t("cards.decisionsRecorded", { accepted, candidates }),
      refactoringEmpty: t("cards.refactoringEmpty"),
      refactoringOpen: (count) => t("cards.refactoringOpen", { count }),
      testsCoverage: t("cards.testsCoverage"),
      testsUnknown: t("cards.testsUnknown"),
      testsNone: t("cards.testsNone"),
      exploreLabels: {
        docs: t("explore.labels.docs"),
        chat: t("explore.labels.chat"),
        files: t("explore.labels.files"),
        architecture: t("explore.labels.architecture"),
        "code-health": t("explore.labels.codeHealth"),
        "knowledge-graph": t("explore.labels.knowledgeGraph"),
        coupling: t("explore.labels.coupling"),
        commits: t("explore.labels.commits"),
        decisions: t("explore.labels.decisions"),
        contributors: t("explore.labels.contributors"),
        stats: t("explore.labels.stats"),
        costs: t("explore.labels.costs"),
      },
      exploreDocs: (pages, modules) => t("explore.docs", { pages, modules }),
      exploreFiles: (files) => t("explore.files", { files }),
      exploreArchitecture: (entryPoints) => t("explore.architecture", { entryPoints }),
      exploreCodeHealth: (findings) => t("explore.codeHealth", { findings }),
      exploreChat: t("explore.chat"),
      exploreKnowledgeGraph: t("explore.knowledgeGraph"),
      exploreCoupling: t("explore.coupling"),
      exploreCommits: t("explore.commits"),
      exploreDecisions: t("explore.decisions"),
      exploreContributors: t("explore.contributors"),
      exploreStats: t("explore.stats"),
      exploreCosts: t("explore.costs"),
    },
    attention: {
      title: t("attention.title"),
      description: t("attention.description"),
      inventoryTitle: t("attention.inventoryTitle"),
      inventoryDescription: t("attention.inventoryDescription"),
      scopeAreas: (count) => t("attention.scopeAreas", { count }),
      scopeOpen: (count) => t("attention.scopeOpen", { count }),
      areas: {
        // An area this build has no key for keeps the shared English name.
        areaLabel: (key) =>
          t.has(`attention.areas.${key}`) ? t(`attention.areas.${key}`) : attentionSourceLabel(key),
        empty: t("attention.empty"),
        severity: (severity) => t(`attention.severity.${severity}`),
      },
      inventory: {
        healthUnit: t("attention.healthUnit"),
        securityUnit: t("attention.securityUnit"),
        securityElsewhere: (count) => t("attention.securityElsewhere", { count }),
        refactoringUnit: t("attention.refactoringUnit"),
        decisionsUnit: t("attention.decisionsUnit"),
        decisionsDetail: (awaiting, drifting) =>
          t("attention.decisionsDetail", { awaiting, drifting }),
        ownershipUnit: t("attention.ownershipUnit"),
        ownershipDetail: t("attention.ownershipDetail"),
      },
    },
  };
}
