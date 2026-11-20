import type { AgentPromptFlavor, AgentPromptResponse } from "@repowise-dev/types/agent-prompts";
import type { FirstLookPage, FirstLookStampResponse } from "@repowise-dev/types/first-look";
import { apiGet, apiPost } from "./client";

const firstLook = (repoId: string) => `/api/repos/${repoId}/reports/first-look`;

/** The stored first-look report, its staleness and its whole-report prompt for `flavor`. */
export async function getFirstLook(
  repoId: string,
  opts: { flavor?: AgentPromptFlavor } = {},
): Promise<FirstLookPage> {
  return apiGet<FirstLookPage>(firstLook(repoId), { flavor: opts.flavor });
}

/** When the stored report was written, without the report. */
export async function getFirstLookStamp(repoId: string): Promise<FirstLookStampResponse> {
  return apiGet<FirstLookStampResponse>(`${firstLook(repoId)}/stamp`);
}

/** One item's prompt, or the whole report's when `itemId` is null, rendered by core for `flavor`. */
export async function getFirstLookPrompt(
  repoId: string,
  itemId: string | null,
  flavor: AgentPromptFlavor,
): Promise<AgentPromptResponse> {
  return apiGet<AgentPromptResponse>(`${firstLook(repoId)}/prompt`, {
    flavor,
    item: itemId ?? undefined,
  });
}

/** Write the report again with the repository's model: one call. */
export async function rewriteFirstLook(
  repoId: string,
  opts: { flavor?: AgentPromptFlavor } = {},
): Promise<FirstLookPage> {
  return apiPost<FirstLookPage>(`${firstLook(repoId)}/rewrite`, undefined, undefined, {
    flavor: opts.flavor,
  });
}
