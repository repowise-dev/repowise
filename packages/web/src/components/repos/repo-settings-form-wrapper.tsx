"use client";

import { toast } from "sonner";
import { GeneralForm } from "@repowise-dev/ui/settings/general-form";
import { updateRepo } from "@/lib/api/repos";
import type { RepoResponse } from "@/lib/api/types";
import type { RepoSettingsValue } from "@repowise-dev/types/settings";

interface Props {
  repo: RepoResponse;
}

export function RepoSettingsFormWrapper({ repo }: Props) {
  const value: RepoSettingsValue = {
    name: repo.name,
    default_branch: repo.default_branch,
    exclude_patterns:
      (repo.settings?.exclude_patterns as string[] | undefined) ?? [],
  };

  async function handleSubmit(next: RepoSettingsValue) {
    try {
      await updateRepo(repo.id, {
        name: next.name,
        default_branch: next.default_branch,
        settings: { ...repo.settings, exclude_patterns: next.exclude_patterns },
      });
      toast.success("Repository settings saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save settings");
      throw err;
    }
  }

  return (
    <GeneralForm
      value={value}
      onSubmit={handleSubmit}
      localPath={repo.local_path}
      remoteUrl={repo.url ?? undefined}
    />
  );
}
