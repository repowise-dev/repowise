import { apiGet } from "./client";
import type {
  ModuleHealthDetail,
  ModuleHealthSummary,
  Paginated,
} from "./types";

export type ModuleHealthSortKey =
  | "health_score"
  | "hotspot_count"
  | "dead_code_lines"
  | "file_count";

export async function listModuleHealth(
  repoId: string,
  options: { sort?: ModuleHealthSortKey; limit?: number; offset?: number } = {},
): Promise<Paginated<ModuleHealthSummary>> {
  return apiGet<Paginated<ModuleHealthSummary>>(
    `/api/repos/${repoId}/modules/health`,
    options,
  );
}

export async function getModuleHealth(
  repoId: string,
  modulePath: string,
): Promise<ModuleHealthDetail> {
  return apiGet<ModuleHealthDetail>(
    `/api/repos/${repoId}/modules/health/${encodeURIComponent(modulePath)}`,
  );
}
