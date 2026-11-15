import { useMemo } from "react";
import { useTranslations } from "next-intl";
import type { DocsTreeCopy } from "@repowise-dev/ui/docs/docs-tree";
import type { DocsReaderCopy } from "@repowise-dev/ui/docs/docs-reader";

/** The docs tree's copy from `docs.tree.*`, stable per locale. */
export function useDocsTreeCopy(): DocsTreeCopy {
  const t = useTranslations("docs.tree");
  return useMemo(
    () => ({
      searchPlaceholder: t("searchPlaceholder"),
      folderView: t("folderView"),
      domainView: t("domainView"),
      switchToFolderView: t("switchToFolderView"),
      switchToDomainView: t("switchToDomainView"),
      toggleFilters: t("toggleFilters"),
      typeLabel: t("typeLabel"),
      statusLabel: t("statusLabel"),
      all: t("all"),
      status: {
        fresh: t("status.fresh"),
        stale: t("status.stale"),
        outdated: t("status.outdated"),
        unwritten: t("status.unwritten"),
      },
      showFreshnessDots: t("showFreshnessDots"),
      pageCount: (count: number) => t("pageCount", { count }),
      summary: {
        stale: (count: number) => t("summary.stale", { count }),
        outdated: (count: number) => t("summary.outdated", { count }),
        unwritten: (count: number) => t("summary.unwritten", { count }),
      },
      noMatches: t("noMatches"),
      showMore: (next: number, remaining: number) => t("showMore", { next, remaining }),
      stubTitle: t("stubTitle"),
      onboarding: t("onboarding"),
      walkthroughs: (count: number) => t("walkthroughs", { count }),
      supportingPackages: (count: number) => t("supportingPackages", { count }),
      codeReference: t("codeReference"),
      codeReferenceMeta: ({ files, symbols, other }) =>
        t("codeReferenceMeta", { files, symbols, other }),
      referenceApi: t("referenceApi"),
      referenceFiles: t("referenceFiles"),
      referenceInfrastructure: t("referenceInfrastructure"),
      referenceCycles: t("referenceCycles"),
      projectActivity: t("projectActivity"),
    }),
    [t],
  );
}

/** The docs reader's copy from `docs.reader.*`, stable per locale. */
export function useDocsReaderCopy(): DocsReaderCopy {
  const t = useTranslations("docs.reader");
  return useMemo(
    () => ({
      selectPageTitle: t("selectPageTitle"),
      selectPageBody: t("selectPageBody"),
      missingTitle: t("missingTitle"),
      missingBody: (path: React.ReactNode) => t.rich("missingBody", { path: () => path }),
      builtFromIndex: t("builtFromIndex"),
      writtenBy: (model: string) => t("writtenBy", { model }),
      writtenByModel: t("writtenByModel"),
      updated: (relative: string) => t("updated", { relative }),
      inModule: (name: string) => t("inModule", { name }),
      openFilePage: t("openFilePage"),
      openFilePageTitle: (path: string) => t("openFilePageTitle", { path }),
      builtFromSources: (count: number) => t("builtFromSources", { count }),
      viewPage: t("viewPage"),
      viewReference: t("viewReference"),
      viewsLabel: t("viewsLabel"),
      referenceIntro: t("referenceIntro"),
      proseLost: t("proseLost"),
      proseNotWritten: t("proseNotWritten"),
      previous: t("previous"),
      next: t("next"),
      possibleInaccuracies: t("possibleInaccuracies"),
      related: t("related"),
      relatedReasons: {
        imports: t("relatedReasons.imports"),
        "imported-by": t("relatedReasons.importedBy"),
        "co-changes-with": t("relatedReasons.coChangesWith"),
        "same-module": t("relatedReasons.sameModule"),
      },
      moreRelated: (count: number) => t("moreRelated", { count }),
    }),
    [t],
  );
}
