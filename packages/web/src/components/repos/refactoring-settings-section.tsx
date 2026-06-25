"use client";

import useSWR from "swr";
import {
  RefactoringSettingsCard,
  type RefactoringSettingsValue,
} from "@repowise-dev/ui/refactoring";
import {
  getRefactoringSettings,
  updateRefactoringSettings,
  type RefactoringSettings,
} from "@/lib/api/refactoring";
import { ApiClientError } from "@/lib/api/client";

/**
 * Repo settings → code-generation toggle. Reads/writes the `refactoring.llm`
 * block in the repo's config. The endpoint is a local-`serve` capability, so a
 * 404 (no accessible checkout, e.g. hosted) renders a quiet unavailable note
 * rather than an error.
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

  const onSave = async (value: RefactoringSettingsValue) => {
    const saved = await updateRefactoringSettings(repoId, value);
    await mutate(saved, { revalidate: false });
  };

  return (
    <RefactoringSettingsCard
      value={data ?? null}
      onSave={onSave}
      loading={isLoading}
      unavailableReason={unavailable}
    />
  );
}
