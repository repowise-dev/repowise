/**
 * Every repowise setting for one repository: list them with their values and
 * where each came from, and change or reset one. The same registry and JSON as
 * `repowise config ... --format json`.
 */

import type {
  SettingChangeResponse,
  SettingsResponse,
} from "@repowise-dev/types/generated/http";
import { apiGet, apiPatch } from "./client";

export type { SettingChangeResponse, SettingsResponse };

/** Every user-facing setting by area; `includeInternal` adds debug and test ones. */
export async function getSettings(
  repoId: string,
  opts?: { includeInternal?: boolean },
): Promise<SettingsResponse> {
  return apiGet<SettingsResponse>(
    `/api/repos/${encodeURIComponent(repoId)}/settings`,
    opts?.includeInternal ? { all: 1 } : undefined,
  );
}

/** Set one setting. A refusal throws `ApiClientError` with the server's message. */
export async function setSetting(
  repoId: string,
  key: string,
  value: unknown,
): Promise<SettingChangeResponse> {
  return apiPatch<SettingChangeResponse>(`/api/repos/${encodeURIComponent(repoId)}/settings`, {
    key,
    value,
  });
}

/** Put one setting back to its default. */
export async function resetSetting(repoId: string, key: string): Promise<SettingChangeResponse> {
  return apiPatch<SettingChangeResponse>(`/api/repos/${encodeURIComponent(repoId)}/settings`, {
    key,
    reset: true,
  });
}
