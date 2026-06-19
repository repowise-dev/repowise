"use client";

import useSWR from "swr";
import {
  getWorkspace,
  getWorkspaceContracts,
  getWorkspaceCoChanges,
  getWorkspaceSystemGraph,
  getWorkspaceDiagnostics,
  getWorkspaceGraph,
} from "@/lib/api/workspace";
import type {
  WorkspaceResponse,
  WorkspaceContractsResponse,
  WorkspaceCoChangesResponse,
  WorkspaceSystemGraphResponse,
  WorkspaceDiagnosticsResponse,
  WorkspaceGraphResponse,
} from "@/lib/api/types";

export function useWorkspace() {
  const { data, error, isLoading } = useSWR<WorkspaceResponse>(
    "workspace",
    () => getWorkspace(),
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );
  return {
    workspace: data ?? null,
    isWorkspace: data?.is_workspace ?? false,
    isLoading,
    error,
  };
}

export function useWorkspaceContracts(opts?: {
  contract_type?: string;
  repo?: string;
  role?: string;
}) {
  const key = `workspace:contracts:${JSON.stringify(opts ?? {})}`;
  const { data, error, isLoading } = useSWR<WorkspaceContractsResponse>(
    key,
    () => getWorkspaceContracts(opts),
    { revalidateOnFocus: false },
  );
  return { data: data ?? null, isLoading, error };
}

export function useWorkspaceCoChanges(opts?: {
  repo?: string;
  min_strength?: number;
  limit?: number;
}) {
  const key = `workspace:co-changes:${JSON.stringify(opts ?? {})}`;
  const { data, error, isLoading } = useSWR<WorkspaceCoChangesResponse>(
    key,
    () => getWorkspaceCoChanges(opts),
    { revalidateOnFocus: false },
  );
  return { data: data ?? null, isLoading, error };
}

export function useWorkspaceSystemGraph() {
  const { data, error, isLoading } = useSWR<WorkspaceSystemGraphResponse>(
    "workspace:system-graph",
    () => getWorkspaceSystemGraph(),
    { revalidateOnFocus: false },
  );
  return { data: data ?? null, isLoading, error };
}

export function useWorkspaceDiagnostics() {
  const { data, error, isLoading } = useSWR<WorkspaceDiagnosticsResponse>(
    "workspace:diagnostics",
    () => getWorkspaceDiagnostics(),
    { revalidateOnFocus: false },
  );
  return { data: data ?? null, isLoading, error };
}

/** Repo-level graph (carries the per-repo health score used by the map). */
export function useWorkspaceGraph() {
  const { data, error, isLoading } = useSWR<WorkspaceGraphResponse>(
    "workspace:graph",
    () => getWorkspaceGraph(),
    { revalidateOnFocus: false },
  );
  return { data: data ?? null, isLoading, error };
}
