"use client";

import { X } from "lucide-react";
import { ScrollArea } from "../ui/scroll-area";
import { truncatePath } from "../lib/format";
import type { UnclusteredFiles } from "@repowise-dev/types/graph";

/**
 * The files no community claims. Almost all carry no dependency edge, so no
 * import-based grouping can place them; they get a named home outside the
 * ranking instead of an arbitrary attachment.
 */
export function GraphUnclusteredPanel({
  unclustered,
  onClose,
  fileHrefFor,
}: {
  unclustered: UnclusteredFiles;
  onClose: () => void;
  fileHrefFor?: ((path: string) => string) | undefined;
}) {
  const files = unclustered.files ?? [];
  const more = unclustered.file_count - files.length;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-2 border-b border-[var(--color-border-default)] px-4 py-3">
        <div className="min-w-0">
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            Not grouped
          </p>
          <p className="mt-0.5 text-sm font-medium text-[var(--color-text-primary)]">
            {unclustered.file_count} file{unclustered.file_count === 1 ? "" : "s"} in no
            community
          </p>
          <p className="text-[11px] text-[var(--color-text-secondary)]">
            Nothing here imports or is imported by the rest of the repo, so the
            grouping has nothing to place them by.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close ungrouped files"
          className="shrink-0 rounded p-1 transition-colors hover:bg-[var(--color-bg-elevated)]"
        >
          <X className="h-4 w-4 text-[var(--color-text-tertiary)]" />
        </button>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        {/* `w-px min-w-full`: the scroll viewport lays its child out as a
            table, which sizes to the longest path; this pins it to the rail. */}
        <div className="w-px min-w-full space-y-1 px-4 py-3">
          {files.map((path) => (
            <div
              key={path}
              className="flex items-center rounded px-1.5 py-1 hover:bg-[var(--color-bg-elevated)]"
            >
              <div className="min-w-0 flex-1">
                {fileHrefFor ? (
                  <a
                    href={fileHrefFor(path)}
                    title={path}
                    className="block truncate font-mono text-xs text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)] hover:underline"
                  >
                    {truncatePath(path)}
                  </a>
                ) : (
                  <p title={path} className="truncate font-mono text-xs text-[var(--color-text-primary)]">
                    {truncatePath(path)}
                  </p>
                )}
              </div>
            </div>
          ))}
          {more > 0 && (
            <p className="px-1.5 pt-1 text-[11px] text-[var(--color-text-tertiary)]">
              +{more} more, by rank
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
