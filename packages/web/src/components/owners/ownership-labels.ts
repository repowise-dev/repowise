import { useMemo } from "react";
import { useTranslations, type TranslationValues } from "next-intl";
import type { OwnershipLabels } from "@repowise-dev/ui/owners/labels";

// No "use client": the person page builds these on the server with
// getTranslations, the client views through useOwnershipLabels.

/** A translator scoped to a namespace, from useTranslations or getTranslations. */
export type OwnershipTranslator = (key: string, values?: TranslationValues) => string;

const basis = (byLines: boolean) => (byLines ? "lines" : "files");
const yes = (flag: boolean) => (flag ? "yes" : "no");

/**
 * The Ownership views' copy from the catalog. `t` is scoped to
 * `views.contributors.ui`, `tPage` to `views.contributors`.
 */
export function buildOwnershipLabels(t: OwnershipTranslator, tPage: OwnershipTranslator): OwnershipLabels {
  return {
    agentPrompt: t("agentPrompt"),
    promptForGroup: (count) => t("promptForGroup", { count }),
    repositoryRoot: t("repositoryRoot"),
    hotspot: t("hotspot"),
    files: (count) => t("files", { count }),
    showAllFiles: (count) => t("showAllFiles", { count }),
    showAllDirectories: (count) => t("showAllDirectories", { count }),
    noOtherAuthor: t("noOtherAuthor"),
    openInOffboarding: tPage("openInOffboarding"),

    coverageLabel: t("coverageLabel"),
    coverageHint: (byLines, days) => t("coverageHint", { basis: basis(byLines), days }),
    coverageUnit: (byLines) => t("coverageUnit", { basis: basis(byLines) }),
    wellShared: t("wellShared"),
    coverageSentence: (pct, byLines, days) => t("coverageSentence", { pct, basis: basis(byLines), days }),
    singleHolderSentence: (single, files) => t("singleHolderSentence", { single, files }),
    noChanges: (days) => t("noChanges", { days }),
    spreadTitle: t("spreadTitle"),
    spreadDescription: (count) => t("spreadDescription", { count }),
    readHotspots: t("readHotspots"),
    readHotspotsUnit: (count) => t("readHotspotsUnit", { count }),
    readHotspotsWhy: t("readHotspotsWhy"),
    readQuiet: t("readQuiet"),
    readQuietUnit: (count) => t("readQuietUnit", { count }),
    readQuietWhy: (days) => t("readQuietWhy", { days }),
    readTruck: t("readTruck"),
    readTruckWhy: t("readTruckWhy"),

    everyone: t("everyone"),
    everyoneDescription: t("everyoneDescription"),
    people: (count, all) => t("people", { count, all: yes(all) }),
    searchPlaceholder: t("searchPlaceholder"),
    searchLabel: t("searchLabel"),
    showAutomation: (count) => t("showAutomation", { count }),
    noMatch: t("noMatch"),
    clearSearch: t("clearSearch"),
    noPeople: t("noPeople"),
    noPeopleDescription: t("noPeopleDescription"),
    showAllPeople: (count, all) => t("showAllPeople", { count, all: yes(all) }),
    loading: t("loading"),

    risksIntro: (files, hotspots, lowHealth, dirs) => t("risksIntro", { files, hotspots, lowHealth, dirs }),
    risksCleanTitle: (count) => t("risksCleanTitle", { count }),
    risksCleanDescription: t("risksCleanDescription"),
    risksOneHolder: (name) => t("risksOneHolder", { name }),
    risksManyHolders: (holders, name, files) =>
      name ? t("risksManyHoldersTop", { holders, name, files }) : t("risksManyHolders", { holders }),
    risksSevere: (count) => t("risksSevere", { count }),
    risksListLabel: t("risksListLabel"),

    lossIntro: (total, days, shown) =>
      total > shown ? `${t("lossIntro", { total, days })} ${t("lossShowing", { shown })}` : t("lossIntro", { total, days }),
    lossCleanTitle: (days) => t("lossCleanTitle", { days }),
    lossCleanDescription: (people, files) => t("lossCleanDescription", { people, files }),
    lossCarriedTitle: t("lossCarriedTitle"),
    lossCarriedDescription: (days, quiet) => t("lossCarriedDescription", { days, quiet }),
    lossLastCommit: (when) => t("lossLastCommit", { when }),
    lossChanging: (changing, owned) =>
      owned === 1 ? t("lossChangingOne") : t("lossChanging", { changing, owned }),
    lossAtRisk: (files, severe) =>
      severe > 0 ? t("lossAtRiskSevere", { files, severe }) : t("lossAtRisk", { files }),
    lossNoneAtRisk: t("lossNoneAtRisk"),
    lossDirectories: (open, count) => t("lossDirectories", { open: yes(open), count }),
    lossListLabel: t("lossListLabel"),

    leavingLabel: t("leavingLabel"),
    leavingPicker: t("leavingPicker"),
    thresholdLabel: (pct) => t("thresholdLabel", { pct }),
    thresholdHelp: t("thresholdHelp"),
    tryLabel: t("tryLabel"),
    pickPrompt: t("pickPrompt"),
    pickerPlaceholder: t("pickerPlaceholder"),
    removePerson: (name) => t("removePerson", { name }),
    selectedPeople: t("selectedPeople"),
    unknownPeople: t("unknownPeople"),
    nothingDependsTitle: (names) => t("nothingDependsTitle", { names }),
    nothingDependsDescription: (threshold, files, names) =>
      t("nothingDependsDescription", { threshold, files, names }),
    noShareTitle: (names, count) => t("noShareTitle", { names, count }),
    noShareDescription: t("noShareDescription"),
    resultSentence: ({ names, files, threshold, codeShare, severe, kept }) =>
      severe > 0
        ? t("resultSentenceSevere", { names, files, threshold, codeShare, severe, kept })
        : t("resultSentence", { names, files, threshold, codeShare, kept }),
    statAtRisk: t("statAtRisk"),
    statHotspots: t("statHotspots"),
    statHotspotsHint: t("statHotspotsHint"),
    statShare: t("statShare"),
    statShareHint: (nloc, files) => t("statShareHint", { nloc, files }),
    statDirectories: t("statDirectories"),
    heir: (name, files, of) => t("heir", { name, files, of }),
    noHeir: t("noHeir"),
    heirSevere: (count) => t("heirSevere", { count }),
    handoverTitle: t("handoverTitle"),
    handoverDescription: (files, names) => t("handoverDescription", { files, names }),
    offboardingListLabel: t("offboardingListLabel"),
    underOnePercent: t("underOnePercent"),

    dependsTitle: t("dependsTitle"),
    dependsDescription: t("dependsDescription"),
    ownershipLink: t("ownershipLink"),
    filesCarryTitle: t("filesCarryTitle"),
    filesCarryHint: (shown, touched) => t("filesCarryHint", { shown, touched }),
    filesCarryDescription: (sole, shown) =>
      sole > 0 ? t("filesCarryDescription", { sole, shown }) : t("filesCarryDescriptionNone"),
    commitMixTitle: t("commitMixTitle"),
    commitMixDescription: (total) => t("commitMixDescription", { total }),
    commitMixEmptyTitle: t("commitMixEmptyTitle"),
    commitMixEmptyDescription: t("commitMixEmptyDescription"),
    commitMixBarLabel: t("commitMixBarLabel"),
    creditTitle: t("creditTitle"),
    creditLink: t("creditLink"),
    creditSentence: (days, files, findings) => t("creditSentence", { days, files, findings }),
    activityLabel: t("activityLabel"),
    activeLine: (commits, floor) => t("activeLine", { commits, floor: yes(floor) }),
    quietLine: t("quietLine"),
    emailLabel: t("emailLabel"),
    aliasesLabel: t("aliasesLabel"),
    footprintTitle: t("footprintTitle"),
    footprintDescription: t("footprintDescription"),
    footprintEmptyTitle: t("footprintEmptyTitle"),
    footprintEmptyDescription: t("footprintEmptyDescription"),
    footprintBarLabel: t("footprintBarLabel"),
    footprintDetail: (files, pct) => t("footprintDetail", { files, pct }),
    relationshipsTitle: t("relationshipsTitle"),
    relationshipsDescription: t("relationshipsDescription"),
    relationshipsEmptyTitle: t("relationshipsEmptyTitle"),
    relationshipsEmptyDescription: t("relationshipsEmptyDescription"),
    relationshipsAlone: t("relationshipsAlone"),
    relationshipsNext: (others, pct) => t("relationshipsNext", { others, pct }),
    relationshipsOthers: (others) => t("relationshipsOthers", { others }),
    partnerLine: (files, pct) => t("partnerLine", { files, pct }),
    unknownName: t("unknownName"),
    fileTableEmptyTitle: t("fileTableEmptyTitle"),
    fileTableEmptyDescription: t("fileTableEmptyDescription"),
    colFile: t("colFile"),
    colCommits90d: t("colCommits90d"),
    colChurn: t("colChurn"),
    colChurnHint: t("colChurnHint"),
    colTheirCommits: t("colTheirCommits"),
    colTheirCommitsHint: t("colTheirCommitsHint"),
    colTouched: t("colTouched"),
    soleCarriedTitle: t("soleCarriedTitle"),
    fileMeta: (commits, churn, ownPct, touched) => t("fileMeta", { commits, churn, ownPct, touched }),

    colContributor: t("colContributor"),
    colFilesOwned: t("colFilesOwned"),
    colFilesOwnedHint: t("colFilesOwnedHint"),
    colHotspots: t("colHotspots"),
    colHotspotsHint: t("colHotspotsHint"),
    colSoleOwner: t("colSoleOwner"),
    colSoleOwnerHint: t("colSoleOwnerHint"),
    colDeadLines: t("colDeadLines"),
    colDeadLinesHint: t("colDeadLinesHint"),
    colLastTouched: t("colLastTouched"),
    kindPerson: t("kindPerson"),
    kindBot: t("kindBot"),
    kindAgent: t("kindAgent"),
    soleInactiveTitle: t("soleInactiveTitle"),
    atLeastTitle: t("atLeastTitle"),
  };
}

/** The same copy in a client component, memoised on the translators. */
export function useOwnershipLabels(): OwnershipLabels {
  const t = useTranslations("views.contributors.ui");
  const tPage = useTranslations("views.contributors");
  return useMemo(() => buildOwnershipLabels(t, tPage), [t, tPage]);
}
