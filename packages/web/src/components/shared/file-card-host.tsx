"use client";

import { useState, useCallback } from "react";
import { FileCardDialog } from "@repowise-dev/ui/shared/file-card";
import {
  docsPagePath,
  fileEntityPath,
  filePageId,
} from "@repowise-dev/ui/shared/entity";
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
              file: fileEntityPath(`/repos/${repoId}`, next.file_path),
              graph: `/repos/${repoId}/architecture?view=files&node=${encodeURIComponent(next.file_path)}`,
              // `?file=` was read by nothing in the docs surface, so this
              // button silently opened the repo overview — a different file's
              // prose, looking exactly like the link had worked. The reader is
              // `?page=`, keyed by the wiki page id. Most files have no page,
              // and that case now says so by name rather than landing the
              // reader somewhere plausible and wrong.
              docs: docsPagePath(`/repos/${repoId}`, filePageId(next.file_path)),
              symbols: `/repos/${repoId}/architecture?view=symbols&q=${encodeURIComponent(next.file_path)}`,
              blastRadius: `/repos/${repoId}/code-health?tab=impact&file=${encodeURIComponent(next.file_path)}`,
              deadCode: `/repos/${repoId}/code-health?tab=dead-code`,
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
