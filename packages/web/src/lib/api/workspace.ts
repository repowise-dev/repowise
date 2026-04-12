import { apiGet } from "./client";
import type {
  WorkspaceResponse,
  WorkspaceContractsResponse,
  WorkspaceCoChangesResponse,
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
  if (opts?.limit) params.limit = String(opts.limit);
  if (opts?.offset) params.offset = String(opts.offset);
  return apiGet<WorkspaceContractsResponse>("/api/workspace/contracts", params);
}

export async function getWorkspaceCoChanges(opts?: {
  repo?: string;
  min_strength?: number;
  limit?: number;
}): Promise<WorkspaceCoChangesResponse> {
  const params: Record<string, string> = {};
  if (opts?.repo) params.repo = opts.repo;
  if (opts?.min_strength) params.min_strength = String(opts.min_strength);
  if (opts?.limit) params.limit = String(opts.limit);
  return apiGet<WorkspaceCoChangesResponse>("/api/workspace/co-changes", params);
}
