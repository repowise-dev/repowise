import { useMemo } from "react";
import { useTranslations } from "next-intl";
import type { GraphFlowCopy } from "@repowise-dev/ui/graph/graph-flow";
import type { GraphTruncationBannerCopy } from "@repowise-dev/ui/graph/graph-truncation-banner";
import type {
  GraphScopeSwitcherCopy,
  GraphNarrowingSelectCopy,
} from "@repowise-dev/ui/graph/graph-scope-controls";

export interface GraphMapCopy {
  flow: GraphFlowCopy;
  truncation: GraphTruncationBannerCopy;
  scope: GraphScopeSwitcherCopy;
  narrowing: GraphNarrowingSelectCopy;
}

/** The Architecture map's chrome from `architecture.map.*`, stable per locale. */
export function useGraphMapCopy(): GraphMapCopy {
  const t = useTranslations("architecture.map");
  return useMemo<GraphMapCopy>(() => {
    const communityFallback = (id: number) => t("communityFallback", { id });
    return {
      flow: {
        allCommunities: t("allCommunities"),
        insideCommunity: t("insideCommunity"),
        openFileHint: t("openFileHint"),
        communityFallback,
        toolbar: {
          trace: t("toolbar.trace"),
          traceTitle: t("toolbar.traceTitle"),
          traceTitleFlowsOnly: t("toolbar.traceTitleFlowsOnly"),
          traceAriaPathOpen: t("toolbar.traceAriaPathOpen"),
          traceAriaFlowsOpen: t("toolbar.traceAriaFlowsOpen"),
          findPath: t("toolbar.findPath"),
          executionFlows: t("toolbar.executionFlows"),
          searchPlaceholder: t("toolbar.searchPlaceholder"),
          searchLabel: t("toolbar.searchLabel"),
          clearSearch: t("toolbar.clearSearch"),
        },
        nodeFilter: {
          label: t("nodeFilter.label"),
          filters: {
            all: { label: t("nodeFilter.all"), hint: t("nodeFilter.allHint") },
            hot: { label: t("nodeFilter.hot"), hint: t("nodeFilter.hotHint") },
            dead: { label: t("nodeFilter.dead"), hint: t("nodeFilter.deadHint") },
          },
        },
        population: {
          allCounted: (total: number) => t("population.allCounted", { total }),
          someCounted: (visible: number, total: number) =>
            t("population.someCounted", { visible, total }),
          allFiles: t("population.allFiles"),
          productionFiles: t("population.productionFiles"),
          ariaLabel: (summary: string) => t("population.ariaLabel", { summary }),
          popoverLabel: t("population.popoverLabel"),
          explainer: t("population.explainer"),
          rows: {
            tests: t("population.tests"),
            examples: t("population.examples"),
            docs: t("population.docs"),
          },
        },
        legend: {
          communities: (count: number) => t("legend.communities", { count }),
          noCommunities: t("legend.noCommunities"),
          smallerNotListed: (count: number) => t("legend.smallerNotListed", { count }),
          notGrouped: t("legend.notGrouped"),
          notGroupedTitle: t("legend.notGroupedTitle"),
          scopedSummary: (members: number, boundary: number, edges: number) =>
            [
              t("legend.scopedFiles", { count: members }),
              boundary > 0 ? t("legend.scopedOutside", { count: boundary }) : null,
              t("legend.scopedEdges", { count: edges }),
            ]
              .filter(Boolean)
              .join(" · "),
          outsideGroup: t("legend.outsideGroup"),
          overviewFiles: (count: number) => t("legend.overviewFiles", { count }),
          overviewThirdParty: (count: number) => t("legend.overviewThirdParty", { count }),
          overviewDependencies: (count: number) =>
            t("legend.overviewDependencies", { count }),
          nodesEdges: (nodes: number, edges: number) =>
            t("legend.nodesEdges", { nodes, edges }),
          thirdPartyShown: t("legend.thirdPartyShown"),
          thirdPartyHidden: (count: number) => t("legend.thirdPartyHidden", { count }),
          show: t("legend.show"),
          hide: t("legend.hide"),
          showThirdParty: t("legend.showThirdParty"),
          hideThirdParty: t("legend.hideThirdParty"),
          unlinkedNotDrawn: (count: number) => t("legend.unlinkedNotDrawn", { count }),
          unlinkedTitle: t("legend.unlinkedTitle"),
          weakerLinksNotDrawn: (count: number) => t("legend.weakerLinksNotDrawn", { count }),
          weakerLinksTitle: t("legend.weakerLinksTitle"),
          languageGroup: t("legend.languageGroup"),
          communityGroup: t("legend.communityGroup"),
          edgesGroup: t("legend.edgesGroup"),
          focusGroup: t("legend.focusGroup"),
          configLanguage: t("legend.configLanguage"),
          otherLanguage: t("legend.otherLanguage"),
          deselectAll: t("legend.deselectAll"),
          selectAll: t("legend.selectAll"),
          communityPlaceholder: communityFallback,
          edgeKinds: {
            crossCommunity: t("legend.edgeKinds.crossCommunity"),
            internal: t("legend.edgeKinds.internal"),
            dynamic: t("legend.edgeKinds.dynamic"),
            lowConfidence: t("legend.edgeKinds.lowConfidence"),
          },
          scopedEdgeKinds: {
            internal: t("legend.scopedEdgeKinds.internal"),
            crossCommunity: t("legend.scopedEdgeKinds.crossCommunity"),
            dynamic: t("legend.edgeKinds.dynamic"),
            lowConfidence: t("legend.edgeKinds.lowConfidence"),
          },
          overviewEdgeKinds: {
            crossCommunity: t("legend.overviewEdgeKinds.crossCommunity"),
            internal: t("legend.overviewEdgeKinds.internal"),
          },
          imports: t("legend.imports"),
          importedBy: t("legend.importedBy"),
          toggleEdges: (label: string) => t("legend.toggleEdges", { label }),
          toggleCommunity: (label: string) => t("legend.toggleCommunity", { label }),
          viewCaptions: {
            dead: t("legend.viewCaptions.dead"),
            hotfiles: t("legend.viewCaptions.hotfiles"),
            unified: t("legend.viewCaptions.unified"),
          },
        },
      },
      truncation: {
        showing: (shown, total) =>
          t.rich("truncation.showing", { shown: () => shown, total: () => total }),
        slowerHint: t("truncation.slowerHint"),
        seeAllGrouped: t("truncation.seeAllGrouped"),
        loadMore: (count: string) => t("truncation.loadMore", { count }),
      },
      scope: {
        label: t("scope.label"),
        scopes: {
          communities: { label: t("scope.communities"), hint: t("scope.communitiesHint") },
          files: { label: t("scope.files"), hint: t("scope.filesHint") },
          tree: { label: t("scope.tree"), hint: t("scope.treeHint") },
        },
      },
      narrowing: {
        showing: t("narrowing.showing"),
        allFiles: t("narrowing.allFiles"),
        community: t("narrowing.community"),
        module: t("narrowing.module"),
        smallerModules: t("narrowing.smallerModules"),
        communityFallback,
      },
    };
  }, [t]);
}
