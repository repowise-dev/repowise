import type { AgentPromptFlavor, AgentPromptResponse } from "@repowise-dev/types/agent-prompts";
import type {
  FirstLookMarkRequest,
  FirstLookMarkResponse,
  FirstLookPage,
} from "@repowise-dev/types/first-look";
import { apiRequest, withQuery, type ApiRequest } from "./client";

/**
 * The repo report requests over *request*, under *base*: the report root for
 * a repository (`/api/repos/{id}/reports/first-look` here). A host with its
 * own fetch layer and routes builds its client from this, so paths, query
 * names and types stay in one place.
 */
export function createFirstLookApi(request: ApiRequest, base: (repoId: string) => string) {
  return {
    /** The repo report now, each item's status against the first look, and the open items' prompt for `flavor`. */
    getFirstLook: (repoId: string, opts: { flavor?: AgentPromptFlavor } = {}) =>
      request<FirstLookPage>(withQuery(base(repoId), { flavor: opts.flavor })),

    /** One item's prompt, one section's, or the whole report's (null), rendered by core for `flavor`. */
    getFirstLookPrompt: (
      repoId: string,
      target: { item: string } | { section: string } | null,
      flavor: AgentPromptFlavor,
    ) =>
      request<AgentPromptResponse>(withQuery(`${base(repoId)}/prompt`, { flavor, ...(target ?? {}) })),

    /** Done, acknowledged, a false positive or snoozed: what `repowise mark` records. */
    markFirstLookItem: (repoId: string, itemId: string, body: FirstLookMarkRequest) =>
      request<FirstLookMarkResponse>(`${base(repoId)}/items/${encodeURIComponent(itemId)}/mark`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),

    /** The report as a Markdown download, relative to the API base. */
    firstLookMarkdownPath: (repoId: string) => `${base(repoId)}/export.md`,

    /** Write the report again with the repository's model: one call. */
    rewriteFirstLook: (repoId: string, opts: { flavor?: AgentPromptFlavor } = {}) =>
      request<FirstLookPage>(withQuery(`${base(repoId)}/rewrite`, { flavor: opts.flavor }), {
        method: "POST",
      }),
  };
}

export const {
  getFirstLook,
  getFirstLookPrompt,
  markFirstLookItem,
  firstLookMarkdownPath,
  rewriteFirstLook,
} = createFirstLookApi(apiRequest, (repoId) => `/api/repos/${repoId}/reports/first-look`);
