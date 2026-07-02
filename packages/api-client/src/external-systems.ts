import type { ExternalSystemsRegistry } from "@repowise-dev/types/external-systems";

import { apiGet } from "./client";

/** Full dependency registry (one row per name + manifest, undeduplicated). */
export async function getExternalSystems(
  repoId: string,
): Promise<ExternalSystemsRegistry> {
  return apiGet<ExternalSystemsRegistry>(
    `/api/repos/${repoId}/external-systems`,
  );
}
