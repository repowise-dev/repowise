/**
 * repowise.dev from the local server: who is using this install, and the
 * one-click publish that runs `repowise publish` for a repo.
 */

import type { IdentityResponse } from "@repowise-dev/types/generated/http";
import { apiGet, apiPost } from "./client";

export type HostedIdentity = IdentityResponse;

/** `repowise publish --format json`, passed through unchanged. */
export interface PublishResult {
  /** `published | curated | needs_app | needs_plan | cap | too_big |
   *  rate_limited | not_github | signed_out | offline | error`. */
  outcome: string;
  message: string;
  /** The one link to act on next, if any. */
  url: string | null;
  details: string[];
  open_url: string | null;
  /** `owner/name` once the remote was read. */
  repo: string | null;
}

export async function getHostedIdentity(): Promise<HostedIdentity> {
  return apiGet<HostedIdentity>("/api/platform/identity");
}

export async function publishRepo(repoId: string): Promise<PublishResult> {
  return apiPost<PublishResult>("/api/platform/publish", { repo_id: repoId });
}
