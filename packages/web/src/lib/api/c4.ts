/**
 * REST client for the C4 endpoints.
 * Backend: packages/server/src/repowise/server/routers/c4.py
 */

import { apiGet } from "./client";
import type { C4L1, C4L2, C4L3 } from "@repowise-dev/ui/c4";

export async function getC4L1(repoId: string): Promise<C4L1> {
  return apiGet<C4L1>(`/api/graph/${repoId}/c4/l1`);
}

export async function getC4L2(repoId: string): Promise<C4L2> {
  return apiGet<C4L2>(`/api/graph/${repoId}/c4/l2`);
}

export async function getC4L3(repoId: string, containerId: string): Promise<C4L3> {
  return apiGet<C4L3>(`/api/graph/${repoId}/c4/l3`, { container_id: containerId });
}

/**
 * Fetch the Mermaid C4 source for the current view. Backend is the source
 * of truth so the exported diagram matches what the API serves.
 */
export async function getC4Mermaid(
  repoId: string,
  level: 1 | 2 | 3,
  containerId?: string | null,
): Promise<string> {
  const params = new URLSearchParams({ level: String(level) });
  if (level === 3 && containerId) params.set("container_id", containerId);
  const apiBase =
    typeof window !== "undefined"
      ? (process.env.NEXT_PUBLIC_REPOWISE_API_URL ?? "")
      : "";
  const apiKey =
    typeof window !== "undefined"
      ? localStorage.getItem("repowise_api_key")
      : null;
  const res = await fetch(`${apiBase}/api/graph/${repoId}/c4/mermaid?${params.toString()}`, {
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch Mermaid C4 source (${res.status})`);
  }
  return res.text();
}
