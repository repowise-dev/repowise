"use client";

import { toast } from "sonner";
import { FileHealthTab, type FindingStatus } from "@repowise-dev/ui/files";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import { updateFindingStatus } from "@/lib/api/code-health";
import type { FileDetailHealth, FunctionBlameRow } from "@repowise-dev/types/files";

interface FileHealthPanelProps {
  repoId: string;
  health: FileDetailHealth;
  functionBlame: FunctionBlameRow[];
}

/**
 * The one client wrapper the file page needs.
 *
 * `FileHealthTab` is the single tab body that hydrates, and the only reason it
 * cannot be handed straight to `buildFilePanels` from the server page is that
 * its triage callback and its two href builders are functions, which do not
 * cross a server boundary as props. Wrapping it here keeps that boundary at one
 * component instead of at the top of the page.
 */
export function FileHealthPanel({ repoId, health, functionBlame }: FileHealthPanelProps) {
  const prefix = `/repos/${repoId}`;

  const onFindingStatusChange = async (findingId: string, status: FindingStatus) => {
    try {
      await updateFindingStatus(repoId, findingId, status);
      toast.success(`Finding marked ${status.replace("_", " ")}`);
    } catch (err) {
      toast.error("Couldn't update finding status");
      throw err;
    }
  };

  return (
    <FileHealthTab
      health={health}
      functionBlame={functionBlame}
      onFindingStatusChange={onFindingStatusChange}
      partnerHref={(p) => fileEntityPath(prefix, p)}
      symbolHref={(s) => symbolEntityPath(prefix, s)}
    />
  );
}
