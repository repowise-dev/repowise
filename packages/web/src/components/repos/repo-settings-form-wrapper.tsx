"use client";

import { useState } from "react";
import { toast } from "sonner";
import { GeneralForm } from "@repowise-dev/ui/settings/general-form";
import { ConfirmDialog } from "@repowise-dev/ui/ui/confirm-dialog";
import { fullResyncRepo, updateRepo } from "@/lib/api/repos";
import type { RepoResponse } from "@/lib/api/types";
import { DEFAULT_WIKI_STYLE } from "@repowise-dev/types";
import type { RepoSettingsValue, WikiStyle } from "@repowise-dev/types/settings";

interface Props {
  repo: RepoResponse;
}

export function RepoSettingsFormWrapper({ repo }: Props) {
  const currentStyle =
    (repo.settings?.wiki_style as WikiStyle | undefined) ?? DEFAULT_WIKI_STYLE;

  const value: RepoSettingsValue = {
    name: repo.name,
    default_branch: repo.default_branch,
    exclude_patterns:
      (repo.settings?.exclude_patterns as string[] | undefined) ?? [],
    wiki_style: currentStyle,
  };

  // When the style changes on save, offer to regenerate the wiki in the new
  // voice (the persisted style only takes effect on the next generation).
  const [regenOpen, setRegenOpen] = useState(false);
  const [pendingStyle, setPendingStyle] = useState<WikiStyle | null>(null);
  const [regenLoading, setRegenLoading] = useState(false);

  async function handleSubmit(next: RepoSettingsValue) {
    const nextStyle = next.wiki_style ?? DEFAULT_WIKI_STYLE;
    try {
      await updateRepo(repo.id, {
        name: next.name,
        default_branch: next.default_branch,
        settings: {
          ...repo.settings,
          exclude_patterns: next.exclude_patterns,
          wiki_style: nextStyle,
        },
      });
      toast.success("Repository settings saved");
      if (nextStyle !== currentStyle) {
        setPendingStyle(nextStyle);
        setRegenOpen(true);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save settings");
      throw err;
    }
  }

  async function handleRegenerate() {
    setRegenLoading(true);
    try {
      await fullResyncRepo(repo.id);
      toast.info("Wiki regeneration queued");
      setRegenOpen(false);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to queue regeneration",
      );
    } finally {
      setRegenLoading(false);
    }
  }

  return (
    <>
      <GeneralForm
        value={value}
        onSubmit={handleSubmit}
        localPath={repo.local_path}
        remoteUrl={repo.url ?? undefined}
      />
      <ConfirmDialog
        open={regenOpen}
        onOpenChange={setRegenOpen}
        title="Regenerate the wiki now?"
        description={`The documentation style was changed to "${pendingStyle ?? ""}". Regenerate the whole wiki now to apply it? This runs LLM generation (a cost). You can also do it later from Sync.`}
        confirmLabel="Regenerate now"
        cancelLabel="Later"
        loading={regenLoading}
        onConfirm={() => void handleRegenerate()}
      />
    </>
  );
}
