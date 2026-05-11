import { apiGet, apiPost } from "./client";
import type {
  WorkspaceResponse,
  WorkspaceContractsResponse,
  WorkspaceCoChangesResponse,
  WorkspaceGraphResponse,
  WorkspaceSyncResponse,
} from "./types";

export async function getWorkspace(): Promise<WorkspaceResponse> {
  return apiGet<WorkspaceResponse>("/api/workspace");
}

export async function getWorkspaceContracts(opts?: {
  contract_type?: string;
  repo?: string;
  role?: string;
  limit?: number;
  offset?: number;
}): Promise<WorkspaceContractsResponse> {
  const params: Record<string, string> = {};
  if (opts?.contract_type) params.contract_type = opts.contract_type;
  if (opts?.repo) params.repo = opts.repo;
  if (opts?.role) params.role = opts.role;
  if (opts?.limit != null) params.limit = String(opts.limit);
  if (opts?.offset != null) params.offset = String(opts.offset);
  return apiGet<WorkspaceContractsResponse>("/api/workspace/contracts", params);
}

export async function getWorkspaceCoChanges(opts?: {
  repo?: string;
  min_strength?: number;
  limit?: number;
}): Promise<WorkspaceCoChangesResponse> {
  const params: Record<string, string> = {};
  if (opts?.repo) params.repo = opts.repo;
  if (opts?.min_strength != null) params.min_strength = String(opts.min_strength);
  if (opts?.limit != null) params.limit = String(opts.limit);
  return apiGet<WorkspaceCoChangesResponse>("/api/workspace/co-changes", params);
}

export async function getWorkspaceGraph(): Promise<WorkspaceGraphResponse> {
  return apiGet<WorkspaceGraphResponse>("/api/workspace/graph");
}

/**
 * Trigger a re-sync (re-index) of the workspace. Pass ``repoAlias`` to
 * scope to one repo; otherwise the server fans out across every loaded
 * workspace repo. Returns one result per repo (accepted / skipped / error).
 */
export async function syncWorkspace(opts?: {
  repoAlias?: string;
  fullResync?: boolean;
}): Promise<WorkspaceSyncResponse> {
  const params: Record<string, string> = {};
  if (opts?.repoAlias) params.repo_alias = opts.repoAlias;
  if (opts?.fullResync) params.full_resync = "true";
  return apiPost<WorkspaceSyncResponse>(
    "/api/workspace/sync",
    undefined,
    undefined,
    params,
  );
}
