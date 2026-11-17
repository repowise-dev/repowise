"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import type { SystemMapNavCopy, SystemMapRouteLoader } from "@repowise-dev/ui/workspace/system-map";
import { getWorkspaceContracts } from "@/lib/api/workspace";

/**
 * Route search for the map: the contract links matching every term. Only the
 * links are read, so one contract row is enough. Ceiling: the endpoint sends
 * every matching link; a short query on a very large estate wants a link
 * limit on the endpoint.
 */
export const loadRouteLinks: SystemMapRouteLoader = async (q) =>
  (await getWorkspaceContracts({ q, limit: 1 })).links;

/** The map's navigation words from the catalog. */
export function useSystemMapNavCopy(): SystemMapNavCopy {
  const t = useTranslations("systemMap.nav");
  return useMemo(
    () => ({
      searchLabel: t("searchLabel"),
      searchPlaceholder: t("searchPlaceholder"),
      searchServices: t("searchServices"),
      searchGroups: t("searchGroups"),
      searchRoutes: t("searchRoutes"),
      searchNone: t("searchNone"),
      searchRoutesLoading: t("searchRoutesLoading"),
      routeCallers: (consumer, others) =>
        others > 0 ? t("routeCallers", { consumer, others }) : t("routeCaller", { consumer }),
      routeProvider: (provider) => t("routeProvider", { provider }),
      displayLabel: t("displayLabel"),
      displayMap: t("displayMap"),
      displayList: t("displayList"),
      helpOpen: t("helpOpen"),
      helpTitle: t("helpTitle"),
      keySearch: t("keySearch"),
      keyBack: t("keyBack"),
      keyFocus: t("keyFocus"),
      keyBetween: t("keyBetween"),
      keyList: t("keyList"),
      keyHelp: t("keyHelp"),
      keySteps: t("keySteps"),
      keyPick: t("keyPick"),
      listCaption: t("listCaption"),
      colService: t("colService"),
      colGroup: t("colGroup"),
      colHealth: t("colHealth"),
      colIn: t("colIn"),
      colOut: t("colOut"),
      colContracts: t("colContracts"),
      noScore: t("noScore"),
      contractsCell: (provided, consumed) => t("contractsCell", { provided, consumed }),
      announceEstate: (services, groups) =>
        groups > 0 ? t("announceEstateGrouped", { services, groups }) : t("announceEstate", { services }),
      announceGroup: (label, services) => t("announceGroup", { label, services }),
      announceFocus: (name, callers, dependencies) => t("announceFocus", { name, callers, dependencies }),
      announceBetween: (a, b) => t("announceBetween", { a, b }),
      announceList: (services) => t("announceList", { services }),
      announcePick: (name) => t("announcePick", { name }),
    }),
    [t],
  );
}
