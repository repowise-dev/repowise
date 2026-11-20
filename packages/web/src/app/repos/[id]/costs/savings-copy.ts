import { useMemo } from "react";
import { useTranslations } from "next-intl";
import {
  CHECK_TOOL_IDS,
  humanizeOperation,
  type SavingsChecksCopy,
  type SavingsCopy,
  type SavingsJobId,
} from "@repowise-dev/ui/savings";
import { RELATIVE_TIME_WORDS, type RelativeTimeUnit, type RelativeTimeWords } from "@repowise-dev/ui/lib/format";

const JOBS: SavingsJobId[] = ["trim", "search", "checks", "answers", "rereads"];

/** The "Checks your agents ran" words from `views.costs.checks`, ages from `overview.ago`. */
function useChecksCopy(): SavingsChecksCopy {
  const t = useTranslations("views.costs.checks");
  const tAgo = useTranslations("overview.ago");
  return useMemo<SavingsChecksCopy>(
    () => ({
      title: t("title"),
      sub: t("sub"),
      riskyLabel: t("riskyLabel"),
      riskySub: (checked) => t("riskySub", { checked }),
      decisionsLabel: t("decisionsLabel"),
      decisionsSub: t("decisionsSub"),
      problemsLabel: t("problemsLabel"),
      problemsSub: t("problemsSub"),
      tools: Object.fromEntries(
        CHECK_TOOL_IDS.map((id) => [
          id,
          { name: t(`tools.${id}.name`), does: t(`tools.${id}.does`), unit: t(`tools.${id}.unit`) },
        ]),
      ) as SavingsChecksCopy["tools"],
      levels: { high: t("levelHigh"), medium: t("levelMedium"), low: t("levelLow") },
      changeHigh: (n) => t("changeHigh", { n }),
      unranked: (n) => t("unranked", { n }),
      whyAccepted: (n) => t("whyAccepted", { n }),
      whyCandidate: (n) => t("whyCandidate", { n }),
      whyNone: (n) => t("whyNone", { n }),
      riskHigh: (n) => t("riskHigh", { n }),
      healthFindings: (n) => t("healthFindings", { n }),
      healthFiles: (n) => t("healthFiles", { n }),
      deadItems: (n) => t("deadItems", { n }),
      deadHigh: (n) => t("deadHigh", { n }),
      links: {
        get_why: t("linkWhy"),
        get_health: t("linkHealth"),
        get_risk: t("linkRisk"),
        get_dead_code: t("linkDeadCode"),
      },
      notRecorded: t("notRecorded"),
      notUsed: t("notUsed"),
      missingFor: (agents, count) => t("missingFor", { agents, count }),
      hasIt: (agent) => t("hasIt", { agent }),
      lacksIt: (agent) => t("lacksIt", { agent }),
      addFor: (agent) => t("addFor", { agent }),
      notListedTo: (agents) => t("notListedTo", { agents }),
      addInSettings: t("addInSettings"),
      agentList: (names) =>
        names.length <= 1
          ? (names[0] ?? "")
          : t("listAnd", { rest: names.slice(0, -1).join(", "), last: names[names.length - 1]! }),
      note: t("note"),
      oldNote: t("oldNote"),
      empty: t("empty"),
      connect: t("connect"),
      agentsTitle: t("agentsTitle"),
      tradeoff: (core, standard) => t("tradeoff", { core, standard }),
      colAgent: t("colAgent"),
      colProfile: t("colProfile"),
      colTools: t("colTools"),
      colLastCall: t("colLastCall"),
      profilePill: (profile, count) => t("profilePill", { profile, count }),
      customProfile: t("customProfile"),
      noCalls: t("noCalls"),
      changeTools: t("changeTools"),
      changeHint: t("changeHint"),
      switchHint: t("switchHint"),
      ago: Object.fromEntries(
        (Object.keys(RELATIVE_TIME_WORDS) as RelativeTimeUnit[]).map((u) => [u, tAgo.raw(u) as string]),
      ) as RelativeTimeWords,
    }),
    [t, tAgo],
  );
}

/** The savings body's words from the `views.costs` catalog. */
export function useSavingsCopy(): SavingsCopy {
  const t = useTranslations("views.costs");
  const checks = useChecksCopy();
  return useMemo<SavingsCopy>(
    () => ({
      heroSince: (date) => t("heroSince", { date }),
      heroWindow: (days) => t("heroWindow", { days }),
      heroUnit: t("heroUnit"),
      heroTokensUnit: t("heroTokensUnit"),
      heroLede: (calls, tokens, files) =>
        files ? t("heroLedeFiles", { calls, tokens, files }) : t("heroLede", { calls, tokens }),
      heroBasis: t("heroBasis"),
      stripBase: t("stripBase"),
      stripWith: t("stripWith"),
      stripTokens: (tokens) => t("stripTokens", { tokens }),
      stripCoverage: (calls) => t("stripCoverage", { calls }),
      tokensLabel: t("tokensLabel"),
      tokensSub: t("tokensSub"),
      tokensSubOutput: (output) => t("tokensSubOutput", { output }),
      filesLabel: t("filesLabel"),
      filesSub: (pct) => t("filesSub", { pct }),
      filesSinceShort: (date) => t("filesSinceShort", { date }),
      billLabel: t("billLabel"),
      billSub: t("billSub"),
      billSubOutput: (usd) => t("billSubOutput", { usd }),
      billNone: t("billNone"),
      overTimeTitle: t("overTimeTitle"),
      lensLabel: t("lensLabel"),
      lens: { usd: t("lensUsd"), tokens: t("lensTokens"), files: t("lensFiles") },
      lensUsdOff: t("lensUsdOff"),
      lensFilesOff: t("lensFilesOff"),
      filesSince: (date) => t("filesSince", { date }),
      tooFewDays: t("tooFewDays"),
      chartAria: (lens, days) => t("chartAria", { lens, days }),
      jobsTitle: t("jobsTitle"),
      jobsSub: t("jobsSub"),
      jobs: Object.fromEntries(
        JOBS.map((id) => [id, { name: t(`jobs.${id}.name`), detail: t(`jobs.${id}.detail`) }]),
      ) as SavingsCopy["jobs"],
      jobTokens: (tokens) => t("jobTokens", { tokens }),
      jobNotSetUp: t("jobNotSetUp"),
      jobNone: t("jobNone"),
      checks,
      usageTitle: t("usageTitle"),
      usageSub: t("usageSub"),
      usageGroupLabel: t("usageGroupLabel"),
      usageGroups: { operation: t("groupOperation"), model: t("groupModel"), day: t("groupDay") },
      spent: t("spent"),
      calls: t("calls"),
      inputTokens: t("inputTokens"),
      outputTokens: t("outputTokens"),
      cost: t("cost"),
      total: t("total"),
      noApiCharges: t("noApiCharges"),
      onSubscription: t("onSubscription"),
      onMachine: t("onMachine"),
      noModelCalls: t("noModelCalls"),
      daysCapped: (shown) => t("daysCapped", { shown }),
      activity: (op) => (t.has(`activities.${op}`) ? t(`activities.${op}`) : humanizeOperation(op)),
      methodTitle: t("methodTitle"),
      measuredTitle: (tokens) => t("measuredTitle", { tokens }),
      measuredBody: t("measuredBody"),
      inferredTitle: (tokens) => t("inferredTitle", { tokens }),
      inferredBody: t("inferredBody"),
      pricedTitle: t("pricedTitle"),
      pricedBody: t("pricedBody"),
      benchmarkLink: t("benchmarkLink"),
      methodLink: t("methodLink"),
      freshMicro: t("freshMicro"),
      freshTitle: t("freshTitle"),
      freshBody: t("freshBody"),
      costOnlyMicro: t("costOnlyMicro"),
      costOnlyTitle: t("costOnlyTitle"),
      costOnlyBody: t("costOnlyBody"),
      emptyBase: t("emptyBase"),
      nudgeDismiss: t("nudgeDismiss"),
    }),
    [t, checks],
  );
}
