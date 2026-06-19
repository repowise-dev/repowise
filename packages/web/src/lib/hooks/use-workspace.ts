"use client";

import useSWR from "swr";
import {
  getWorkspace,
  getWorkspaceContracts,
  getWorkspaceCoChanges,
  getWorkspaceSystemGraph,
  getWorkspaceGraph,
  getWorkspaceBlastRadius,
  getWorkspaceBreakingChanges,
  getWorkspaceConformance,
} from "@/lib/api/workspace";
import type {
  WorkspaceResponse,
  WorkspaceContractsResponse,
  WorkspaceCoChangesResponse,
  WorkspaceSystemGraphResponse,
  WorkspaceGraphResponse,
  WorkspaceBlastRadiusResponse,
  WorkspaceBreakingChangesResponse,
  WorkspaceConformanceResponse,
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

/**
 * Cross-repo blast radius for a selected target service. Pass ``null`` to skip
 * the request (no target selected).
 */
export function useWorkspaceBlastRadius(
  target: string | null,
  opts?: { maxDepth?: number; includeBehavioral?: boolean },
) {
  const key = target
    ? `workspace:blast-radius:${target}:${opts?.maxDepth ?? ""}:${opts?.includeBehavioral ?? ""}`
    : null;
  const { data, error, isLoading } = useSWR<WorkspaceBlastRadiusResponse>(
    key,
    () =>
      getWorkspaceBlastRadius({
        target: target as string,
        ...(opts?.maxDepth != null ? { maxDepth: opts.maxDepth } : {}),
        ...(opts?.includeBehavioral != null ? { includeBehavioral: opts.includeBehavioral } : {}),
      }),
    { revalidateOnFocus: false },
  );
  return { data: data ?? null, isLoading, error };
}

/**
 * Breaking-change report from the most recent workspace update. Pass
 * ``enabled=false`` to skip the request until the user opens the panel.
 */
export function useWorkspaceBreakingChanges(enabled = true) {
  const { data, error, isLoading } = useSWR<WorkspaceBreakingChangesResponse>(
    enabled ? "workspace:breaking-changes" : null,
    () => getWorkspaceBreakingChanges(),
    { revalidateOnFocus: false },
  );
  return { data: data ?? null, isLoading, error };
}

/**
 * Architecture conformance report from the most recent workspace update. Pass
 * ``enabled=false`` to skip the request until the view needs it.
 */
export function useWorkspaceConformance(enabled = true) {
  const { data, error, isLoading } = useSWR<WorkspaceConformanceResponse>(
    enabled ? "workspace:conformance" : null,
    () => getWorkspaceConformance(),
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
