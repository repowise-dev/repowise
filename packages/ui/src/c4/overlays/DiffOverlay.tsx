"use client";

import type React from "react";
import { useArchitectureStore } from "../store/use-architecture-store";

export function useDiffNodeStyle(
  nodeId: string,
): { diffState: "changed" | "affected" | "faded" } | null {
  const diffMode = useArchitectureStore((s) => s.diffMode);
  const changedNodeIds = useArchitectureStore((s) => s.changedNodeIds);
  const affectedNodeIds = useArchitectureStore((s) => s.affectedNodeIds);

  if (!diffMode) return null;
  if (changedNodeIds.has(nodeId)) return { diffState: "changed" };
  if (affectedNodeIds.has(nodeId)) return { diffState: "affected" };
  return { diffState: "faded" };
}

export function DiffOverlay(): React.ReactElement | null {
  return null;
}
