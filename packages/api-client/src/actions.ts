import type {
  ActionsResponse,
  ActionStateRequest,
  ActionStateResponse,
  WorkspaceActionsResponse,
} from "@repowise-dev/types/actions";
import { apiGet, apiPut } from "./client";

/** Both horizons in one call, so the week and quarter lenses switch without a fetch. */
export async function getActions(repoId: string): Promise<ActionsResponse> {
  return apiGet<ActionsResponse>(`/api/repos/${repoId}/actions`);
}

export async function setActionState(
  repoId: string,
  actionId: string,
  body: ActionStateRequest,
): Promise<ActionStateResponse> {
  return apiPut<ActionStateResponse>(
    `/api/repos/${repoId}/actions/${encodeURIComponent(actionId)}/state`,
    body,
  );
}

/** Each workspace repository's lead actions, plus the cross-repository ones. */
export async function getWorkspaceActions(): Promise<WorkspaceActionsResponse> {
  return apiGet<WorkspaceActionsResponse>("/api/workspace/actions");
}
