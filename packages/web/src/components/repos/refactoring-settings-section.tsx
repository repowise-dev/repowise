"use client";

import useSWR from "swr";
import { RefactoringSettingsCard } from "@repowise-dev/ui/refactoring";
import {
  getRefactoringSettings,
  updateRefactoringSettings,
  type RefactoringSettings,
} from "@/lib/api/refactoring";
import { ApiClientError } from "@/lib/api/client";
import { providerSetupHref } from "@/lib/utils/page-href";

/**
 * Repo settings → code-generation toggle. Writes `refactoring.llm.enabled` and
 * shows the provider/model chat resolves, which generation reuses. The endpoint
 * is a local-`serve` capability, so a 404 (no accessible checkout, e.g. hosted)
 * renders a quiet unavailable note rather than an error.
 */
export function RefactoringSettingsSection({ repoId }: { repoId: string }) {
  const { data, error, isLoading, mutate } = useSWR<RefactoringSettings>(
    `refactoring-settings:${repoId}`,
    () => getRefactoringSettings(repoId),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );

  const unavailable =
    error instanceof ApiClientError && error.status === 404
      ? "Code generation is only available when the repository is served from a local checkout."
      : error
        ? "Could not load code-generation settings."
        : null;

  const onToggle = async (enabled: boolean) => {
    const saved = await updateRefactoringSettings(repoId, enabled);
    await mutate(saved, { revalidate: false });
  };

  return (
    <RefactoringSettingsCard
      value={data ?? null}
      onToggle={onToggle}
      setupHref={providerSetupHref(repoId)}
      loading={isLoading}
      unavailableReason={unavailable}
    />
  );
}
