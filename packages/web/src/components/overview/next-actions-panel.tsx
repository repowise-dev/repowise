"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo } from "react";
import { useTranslations } from "next-intl";
import useSWR from "swr";
import type { ActionsResponse, NextAction } from "@repowise-dev/types/actions";
import type { AgentPromptFlavor } from "@repowise-dev/types/agent-prompts";
import { NextActions } from "@repowise-dev/ui/overview/next-actions";
import { actionHref, type NextActionsCopy } from "@repowise-dev/ui/overview";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { getActionPrompt, getActions, setActionState } from "@/lib/api/actions";

/** The list's own words in the active locale. The actions' text is the server's. */
function useNextActionsCopy(): NextActionsCopy {
  const t = useTranslations("overview.next");
  const tSeverity = useTranslations("overview.attention.severity");
  return useMemo(
    () => ({
      title: t("title"),
      titleCount: (count) => t("titleCount", { count }),
      timeFrame: t("timeFrame"),
      week: t("week"),
      weekHint: t("weekHint"),
      quarter: t("quarter"),
      quarterHint: t("quarterHint"),
      tier: (tier) => (t.has(`tier.${tier}`) ? t(`tier.${tier}`) : tier),
      allAnswered: t("allAnswered"),
      showFewer: t("showFewer"),
      showAll: (count) => t("showAll", { count }),
      listing: (shown, total) => t("listing", { shown, total }),
      answered: (count) => t("answered", { count }),
      notChecked: (checks) => t("notChecked", { checks }),
      emptyChecks: (count) => t("emptyChecks", { count }),
      undo: t("undo"),
      undoFailed: t("undoFailed"),
      saveFailed: t("saveFailed"),
      openDetails: t("openDetails"),
      markDone: t("markDone"),
      markedDone: t("markedDone"),
      snooze: t("snooze"),
      snoozed: t("snoozed"),
      dismiss: t("dismiss"),
      dismissed: t("dismissed"),
      effort: (effort) => t(`effort.${effort}`),
      worthCheck: t("worthCheck"),
      inferred: t("inferred"),
      openRow: (title) => t("openRow", { title }),
      moreFor: (title) => t("moreFor", { title }),
      // Same words as the attention rows' severity.
      severity: (severity) => tSeverity(severity),
    }),
    [t, tSeverity],
  );
}

/**
 * The local app's binding for "Do next": its routes and its write path. The
 * list itself is the shared component; its words come from `overview.next`.
 */
export function NextActionsPanel({ repoId, data }: { repoId: string; data: ActionsResponse }) {
  // The server-rendered view seeds the key; after an answer, revalidating it
  // re-reads the list so the sentence and counts say what is left. SWR keeps
  // only the latest response and keeps the last good view when a read fails.
  const key = `actions:${repoId}`;
  const { data: view, mutate } = useSWR(key, () => getActions(repoId), {
    fallbackData: data,
    revalidateOnMount: false,
    revalidateOnFocus: false,
  });
  // A newer server render (navigation, refresh) replaces whatever was cached.
  useEffect(() => {
    void mutate(data, { revalidate: false });
  }, [data, mutate]);
  const base = `/repos/${repoId}`;
  const copy = useNextActionsCopy();
  const tNext = useTranslations("overview.next");
  const loadPrompt = useCallback(
    async (action: NextAction, flavor: AgentPromptFlavor) =>
      (await getActionPrompt(repoId, action.id, { flavor })).text,
    [repoId],
  );
  return (
    <NextActions
      data={view ?? data}
      defaultOpen={false}
      copy={copy}
      unavailableHint={tNext.rich("runUpdate", {
        code: (chunks) => <code className="font-mono text-[0.85em]">{chunks}</code>,
      })}
      loadPrompt={loadPrompt}
      LinkComponent={Link}
      hrefFor={(action) => actionHref(action, base)}
      fileHref={(path) => fileEntityPath(base, path)}
      onSetState={async (action, state) => {
        await setActionState(repoId, action.id, {
          state,
          fingerprint: action.fingerprint,
        });
        void mutate();
      }}
    />
  );
}
