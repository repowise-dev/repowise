/**
 * The Setup page and the welcome: what is set up for a repo, and the switches
 * that change it. Writes run the matching CLI command on the server and are
 * accepted only from the local web UI of a server with no API key.
 */

import type {
  OnboardingPatch,
  OnboardingState,
  SetupResponse,
  SetupWriteResponse,
} from "@repowise-dev/types/generated/http";
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "./client";

export type { OnboardingPatch, OnboardingState, SetupResponse, SetupWriteResponse };

/** Hooks the Setup page can switch: auto-sync after commits, and distill rewrites. */
export type SetupHookName = "post-commit" | "rewrite";

/** Index, agents, hooks, coverage, model and doctor checks for one repo. */
export async function getRepoSetup(repoId: string): Promise<SetupResponse> {
  return apiGet<SetupResponse>(`/api/repos/${encodeURIComponent(repoId)}/setup`);
}

/** `repowise agents add --target=<agentId>`; the answer names the files written. */
export async function connectSetupAgent(
  repoId: string,
  agentId: string,
): Promise<SetupWriteResponse> {
  return apiPost<SetupWriteResponse>(
    `/api/repos/${encodeURIComponent(repoId)}/setup/agents/${encodeURIComponent(agentId)}`,
  );
}

/** `repowise agents remove --target=<agentId>`. */
export async function disconnectSetupAgent(
  repoId: string,
  agentId: string,
): Promise<SetupWriteResponse> {
  return apiDelete<SetupWriteResponse>(
    `/api/repos/${encodeURIComponent(repoId)}/setup/agents/${encodeURIComponent(agentId)}`,
  );
}

/** `repowise hook install|uninstall`, or `repowise hook rewrite install|uninstall`. */
export async function setSetupHook(
  repoId: string,
  name: SetupHookName,
  enabled: boolean,
): Promise<SetupWriteResponse> {
  return apiPut<SetupWriteResponse>(
    `/api/repos/${encodeURIComponent(repoId)}/setup/hooks/${name}`,
    { enabled },
  );
}

/** `repowise coverage add <path>` for a report file inside the repo. */
export async function addSetupCoverage(
  repoId: string,
  path: string,
): Promise<SetupWriteResponse> {
  return apiPost<SetupWriteResponse>(`/api/repos/${encodeURIComponent(repoId)}/setup/coverage`, {
    path,
  });
}

/** Per-machine onboarding state, without running the repo checks. */
export async function getOnboardingState(): Promise<OnboardingState> {
  return apiGet<OnboardingState>("/api/setup/onboarding");
}

/** Mark the welcome seen for a repo, or hide the setup pill or the hosted card. */
export async function updateOnboardingState(patch: OnboardingPatch): Promise<OnboardingState> {
  return apiPatch<OnboardingState>("/api/setup/onboarding", patch);
}
