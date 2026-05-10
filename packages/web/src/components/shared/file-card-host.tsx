"use client";

import { useState, useCallback } from "react";
import { FileCardDialog } from "@repowise-dev/ui/shared/file-card";
import type { FileCardData, FileCardLinks } from "@repowise-dev/ui/shared/file-card";

/**
 * Stateful host for FileCardDialog. Pages that have a list of files (hotspots,
 * dead code, ownership, search results) can open the universal file card by
 * calling `open(data)` returned from this hook. Links default to the
 * standard per-repo deep links if a `repoId` is provided.
 */
export function useFileCardHost(repoId?: string) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<FileCardData | null>(null);
  const [links, setLinks] = useState<FileCardLinks | undefined>(undefined);

  const showFile = useCallback(
    (next: FileCardData, customLinks?: FileCardLinks) => {
      setData(next);
      const linksToUse: FileCardLinks | undefined =
        customLinks ??
        (repoId
          ? {
              graph: `/repos/${repoId}/graph?node=${encodeURIComponent(next.file_path)}`,
              docs: `/repos/${repoId}/docs?file=${encodeURIComponent(next.file_path)}`,
              symbols: `/repos/${repoId}/symbols?q=${encodeURIComponent(next.file_path)}`,
              blastRadius: `/repos/${repoId}/risk?tab=impact&file=${encodeURIComponent(next.file_path)}`,
              deadCode: `/repos/${repoId}/risk?tab=dead-code`,
              decisions: `/repos/${repoId}/decisions?file=${encodeURIComponent(next.file_path)}`,
            }
          : undefined);
      setLinks(linksToUse);
      setOpen(true);
    },
    [repoId],
  );

  const dialog = (
    <FileCardDialog open={open} onOpenChange={setOpen} data={data} links={links} />
  );

  return { showFile, dialog };
}
