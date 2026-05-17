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
