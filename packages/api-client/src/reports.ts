import type { AgentPromptFlavor, AgentPromptResponse } from "@repowise-dev/types/agent-prompts";
import type {
  FirstLookMarkRequest,
  FirstLookMarkResponse,
  FirstLookPage,
} from "@repowise-dev/types/first-look";
import { apiGet, apiPost, apiPut } from "./client";

const firstLook = (repoId: string) => `/api/repos/${repoId}/reports/first-look`;

/** The repo report now, each item's status against the first look, and the open items' prompt for `flavor`. */
export async function getFirstLook(
  repoId: string,
  opts: { flavor?: AgentPromptFlavor } = {},
): Promise<FirstLookPage> {
  return apiGet<FirstLookPage>(firstLook(repoId), { flavor: opts.flavor });
}

/** One item's prompt, one section's, or the whole report's (null), rendered by core for `flavor`. */
export async function getFirstLookPrompt(
  repoId: string,
  target: { item: string } | { section: string } | null,
  flavor: AgentPromptFlavor,
): Promise<AgentPromptResponse> {
  return apiGet<AgentPromptResponse>(`${firstLook(repoId)}/prompt`, { flavor, ...(target ?? {}) });
}

/** Done, acknowledged, a false positive or snoozed: what `repowise mark` records. */
export async function markFirstLookItem(
  repoId: string,
  itemId: string,
  body: FirstLookMarkRequest,
): Promise<FirstLookMarkResponse> {
  return apiPut<FirstLookMarkResponse>(
    `${firstLook(repoId)}/items/${encodeURIComponent(itemId)}/mark`,
    body,
  );
}

/** The report as a Markdown download, relative to the API base. */
export function firstLookMarkdownPath(repoId: string): string {
  return `${firstLook(repoId)}/export.md`;
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
