"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import type { FirstLookCopy } from "@repowise-dev/ui/report/first-look";

const RECORDS = ["stripTitle", "stripUnit", "table", "status", "severity"] as const;

/** The report page's chrome in the active locale. The report's own sentences are core's. */
export function useFirstLookCopy(): FirstLookCopy {
  const t = useTranslations("views.firstLook.copy");
  return useMemo(() => {
    const record = (key: (typeof RECORDS)[number]) => t.raw(key) as Record<string, string>;
    const s = (key: string) => t(key);
    return {
      eyebrow: s("eyebrow"),
      back: s("back"),
      handToAgent: s("handToAgent"),
      share: s("share"),
      copyLink: s("copyLink"),
      copyLinkHint: s("copyLinkHint"),
      copyForAgent: s("copyForAgent"),
      copyForAgentHint: s("copyForAgentHint"),
      downloadMarkdown: s("downloadMarkdown"),
      downloadMarkdownHint: s("downloadMarkdownHint"),
      savePdf: s("savePdf"),
      savePdfHint: s("savePdfHint"),
      downloadJson: s("downloadJson"),
      downloadJsonHint: s("downloadJsonHint"),
      previewExport: s("previewExport"),
      previewExportHint: s("previewExportHint"),
      exportNote: s("exportNote"),
      exitExport: s("exitExport"),
      copied: s("copied"),
      copyFailed: s("copyFailed"),
      files: (n) => t("files", { n }),
      firstLookOn: (date) => t("firstLookOn", { date }),
      writtenOn: (date) => t("writtenOn", { date }),
      resolved: s("resolved"),
      acknowledged: s("acknowledged"),
      newSince: s("newSince"),
      newSinceDate: (date) => t("newSinceDate", { date }),
      stripTitle: record("stripTitle"),
      stripUnit: record("stripUnit"),
      measured: s("measured"),
      startHere: s("startHere"),
      fixWithAgent: s("fixWithAgent"),
      fixWithAgentHint: s("fixWithAgentHint"),
      copyItemPrompt: s("copyItemPrompt"),
      countOf: (n, total) => t("countOf", { n, total }),
      moreInApp: (n) => t("moreInApp", { n }),
      linesCovered: s("linesCovered"),
      branchesCovered: s("branchesCovered"),
      table: record("table") as FirstLookCopy["table"],
      status: record("status") as FirstLookCopy["status"],
      severity: record("severity") as FirstLookCopy["severity"],
      confidence: (level) => t("confidence", { level }),
      effort: (e) => t("effort", { e }),
      valuesRedacted: s("valuesRedacted"),
      mark: s("mark"),
      markDone: s("markDone"),
      markDoneHint: s("markDoneHint"),
      markAck: s("markAck"),
      markAckHint: s("markAckHint"),
      markFalsePositive: s("markFalsePositive"),
      markFalsePositiveHint: s("markFalsePositiveHint"),
      markSnooze: s("markSnooze"),
      reasonLabel: s("reasonLabel"),
      save: s("save"),
      cancel: s("cancel"),
      reportAt: (sha) => t("reportAt", { sha }),
      sinceThen: s("sinceThen"),
      wordingByModel: (date) => t("wordingByModel", { date }),
      wordingTemplate: s("wordingTemplate"),
      rewrite: s("rewrite"),
      rewriteWithModel: s("rewriteWithModel"),
      writing: s("writing"),
      approxCost: (cost) => t("approxCost", { cost }),
      repoReport: s("repoReport"),
      openReport: s("openReport"),
    };
  }, [t]);
}
