"use client";

import { useCallback } from "react";
import { useTranslations } from "next-intl";
import type { ProviderIssue } from "@repowise-dev/ui/jobs";

/** The values each known issue kind names; a missing one falls back to the
 *  server's English message so no sentence renders with a hole in it. */
const REQUIRED: Record<string, (keyof ProviderIssue)[]> = {
  auth: ["provider"],
  rate_limit: ["provider"],
  model_not_found: ["provider", "model"],
  not_found: ["provider", "model"],
  unreachable: ["target"],
  docs_skipped: [],
};

/** Words a job's provider issue in the active locale, by its kind. */
export function useIssueText(): (issue: ProviderIssue) => string {
  const t = useTranslations("jobs.issue");
  return useCallback(
    (issue: ProviderIssue) => {
      const needs = REQUIRED[issue.kind];
      if (!needs || needs.some((field) => !issue[field])) return issue.message;
      return t(issue.kind, {
        provider: issue.provider ?? "",
        model: issue.model ?? "",
        target: issue.target ?? "",
      });
    },
    [t],
  );
}
