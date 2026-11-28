"use client";

import { useCallback, useMemo } from "react";
import type { PerformanceOpportunity } from "@repowise-dev/types/health";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import {
  buildPerformanceAreaPrompt,
  buildPerformanceOpportunityPrompt,
  buildRefactoringPlanPrompt,
  type AiPromptFlavor,
} from "../ai-prompt-builder";
import type { AiPromptRequest } from "../ai-prompt-action";
import type { PerformanceViewAdapter } from "./adapter";
import { useDetailLoader, useVerifiedPlanLoader } from "./cache";
import { opportunityTitle } from "./presentation";
import { formatNumber } from "../../lib/format";

/**
 * The agent prompts this tab hands over, as requests for the shared prompt
 * action. A cause's prompt is its verified stored plan when there is one,
 * else an evidence handoff built from its detail; both read the same cache
 * entries the drawer reads, so opening the prompt never refetches them.
 */
export function usePerformancePrompts(adapter: PerformanceViewAdapter) {
  const loadDetail = useDetailLoader(adapter);
  const loadPlan = useVerifiedPlanLoader(adapter);

  const forOpportunity = useCallback(
    (row: PerformanceOpportunity): AiPromptRequest => {
      // Resolved once per request, then reused across flavor switches.
      let resolved: Promise<{ opportunity: PerformanceOpportunity; plan: RefactoringPlan | null }> | null =
        null;
      const resolve = () =>
        (resolved ??= (async () => {
          const detail = await loadDetail(row.opportunity_id).catch(() => null);
          const opportunity = detail?.found ? detail : row;
          const plan = await loadPlan(opportunity).catch(() => null);
          return { opportunity, plan };
        })());
      const promptSource = async (flavor: AiPromptFlavor): Promise<string> => {
        const { opportunity, plan } = await resolve();
        if (!plan) return buildPerformanceOpportunityPrompt({ opportunity, flavor });
        // Core renders the plan prompt when the host serves it.
        return adapter.getRefactoringPlanPrompt
          ? adapter.getRefactoringPlanPrompt(plan.id, flavor)
          : buildRefactoringPlanPrompt({ plan, flavor });
      };
      return {
        title: "Prompt for your agent",
        description: `${opportunityTitle(row)}. The stored plan when one names this cause, else the evidence, with the tests to run.`,
        filePath: row.file_path,
        promptSource,
      };
    },
    [adapter, loadDetail, loadPlan],
  );

  const forArea = useCallback(
    (area: string, rows: PerformanceOpportunity[], paged = false): AiPromptRequest => ({
      title: "Prompt for your agent",
      description: `${formatNumber(rows.length)} performance cause${rows.length === 1 ? "" : "s"} in this area${paged ? " on this page" : ""}, in ranked order, one change each.`,
      filePath: area || null,
      getPrompt: (flavor) => buildPerformanceAreaPrompt({ area, opportunities: rows, paged, flavor }),
    }),
    [],
  );

  return useMemo(() => ({ forOpportunity, forArea }), [forOpportunity, forArea]);
}
