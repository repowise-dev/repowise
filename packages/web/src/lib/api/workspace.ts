import { apiGet, apiPost } from "./client";
import type {
  WorkspaceResponse,
  WorkspaceContractsResponse,
  WorkspaceCoChangesResponse,
  WorkspaceGraphResponse,
  WorkspaceSyncResponse,
  WorkspaceSystemGraphResponse,
  WorkspaceBlastRadiusResponse,
  WorkspaceBreakingChangesResponse,
  WorkspaceConformanceResponse,
  WorkspaceArchitectureResponse,
} from "./types";

export async function getWorkspace(
  fetchOptions?: RequestInit,
): Promise<WorkspaceResponse> {
  return apiGet<WorkspaceResponse>("/api/workspace", undefined, fetchOptions);
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
 * The service-granular system graph (nodes = services, typed directed edges)
 * that the Live System Map renders. Thin pass-through over the persisted
 * `system_graph.json` artifact.
 */
export async function getWorkspaceSystemGraph(
  fetchOptions?: RequestInit,
): Promise<WorkspaceSystemGraphResponse> {
  return apiGet<WorkspaceSystemGraphResponse>(
    "/api/workspace/system-graph",
    undefined,
    fetchOptions,
  );
}

/**
 * Cross-repo blast radius from a service node (or repo alias): the downstream
 * services impacted if it changes, ranked by impact. Reads the same system
 * graph the map renders.
 */
export async function getWorkspaceBlastRadius(opts: {
  target: string;
  maxDepth?: number;
  includeBehavioral?: boolean;
}): Promise<WorkspaceBlastRadiusResponse> {
  const params: Record<string, string | number | boolean> = { target: opts.target };
  if (opts.maxDepth != null) params.max_depth = opts.maxDepth;
  if (opts.includeBehavioral != null) params.include_behavioral = opts.includeBehavioral;
  return apiGet<WorkspaceBlastRadiusResponse>("/api/workspace/blast-radius", params);
}

/**
 * Provider contract changes from the most recent update that break consumers
 * across repos, with the impacted consumer files. Optionally filter by provider
 * repo or severity.
 */
export async function getWorkspaceBreakingChanges(opts?: {
  repo?: string;
  severity?: "breaking" | "warning";
}): Promise<WorkspaceBreakingChangesResponse> {
  const params: Record<string, string> = {};
  if (opts?.repo) params.repo = opts.repo;
  if (opts?.severity) params.severity = opts.severity;
  return apiGet<WorkspaceBreakingChangesResponse>("/api/workspace/breaking-changes", params);
}

/**
 * Architecture conformance from the most recent update: dependency-rule
 * violations + dependency cycles over the system graph. Optionally filter to
 * findings involving one repo.
 */
export async function getWorkspaceConformance(opts?: {
  repo?: string;
}): Promise<WorkspaceConformanceResponse> {
  const params: Record<string, string> = {};
  if (opts?.repo) params.repo = opts.repo;
  return apiGet<WorkspaceConformanceResponse>("/api/workspace/conformance", params);
}

export async function getWorkspaceArchitecture(): Promise<WorkspaceArchitectureResponse> {
  return apiGet<WorkspaceArchitectureResponse>("/api/workspace/architecture");
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
