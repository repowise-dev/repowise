"use client";

import useSWR from "swr";
import {
  getWorkspaceSystemGraph,
  getWorkspaceDiagnostics,
  getWorkspaceGraph,
  getWorkspaceBlastRadius,
  getWorkspaceBreakingChanges,
  getWorkspaceConformance,
  getWorkspaceArchitecture,
} from "@/lib/api/workspace";
import type {
  ExtractionDiagnostics,
  WorkspaceSystemGraphResponse,
  WorkspaceGraphResponse,
  WorkspaceBlastRadiusResponse,
  WorkspaceBreakingChangesResponse,
  WorkspaceConformanceResponse,
  WorkspaceArchitectureResponse,
} from "@/lib/api/types";

/**
 * The system graph. ``view="map"`` asks for the map's lean view, which leaves
 * the diagnostics to ``useWorkspaceDiagnostics``; a server without that view
 * answers with the full graph, diagnostics included.
 */
export function useWorkspaceSystemGraph(view?: "map") {
  const { data, error, isLoading } = useSWR<WorkspaceSystemGraphResponse>(
    view ? `workspace:system-graph:${view}` : "workspace:system-graph",
    () => getWorkspaceSystemGraph(undefined, view ? { view } : undefined),
    { revalidateOnFocus: false },
  );
  return { data: data ?? null, isLoading, error };
}

/** Extraction diagnostics. Pass ``enabled=false`` to defer the request. */
export function useWorkspaceDiagnostics(enabled = true) {
  const { data, error, isLoading } = useSWR<ExtractionDiagnostics>(
    enabled ? "workspace:diagnostics" : null,
    () => getWorkspaceDiagnostics(),
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

/**
 * Architecture-complexity metrics (propagation cost, core, 1-10 score, roles)
 * computed from the system graph. Pass ``enabled=false`` to defer the request.
 */
export function useWorkspaceArchitecture(enabled = true) {
  const { data, error, isLoading } = useSWR<WorkspaceArchitectureResponse>(
    enabled ? "workspace:architecture" : null,
    () => getWorkspaceArchitecture(),
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
