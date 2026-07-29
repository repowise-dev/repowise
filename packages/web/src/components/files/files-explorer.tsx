"use client";

import { useCallback } from "react";
import useSWR from "swr";
import { AlertTriangle } from "lucide-react";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { FilesIndex } from "@repowise-dev/ui/files";
import { getFilesIndex } from "@/lib/api/files";

export function FilesExplorer({ repoId }: { repoId: string }) {
  const { data, error, isLoading } = useSWR(
    `files-index:${repoId}`,
    () => getFilesIndex(repoId),
    { revalidateOnFocus: false },
  );

  const fileHref = useCallback(
    (path: string) =>
      `/repos/${repoId}/files/${path.split("/").map(encodeURIComponent).join("/")}`,
    [repoId],
  );

  if (isLoading) {
    return (
      // Shapes match the real layout: header + sentence, the map, its key row,
      // then the table section. A skeleton that draws something the page no
      // longer has reflows when content lands, which reads as slower than
      // showing nothing at all.
      <div>
        <div className="space-y-3">
          <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
            <div className="space-y-2">
              <Skeleton className="h-6 w-44" />
              <Skeleton className="h-4 w-80 max-w-full" />
            </div>
            <Skeleton className="h-8 w-64" />
          </div>
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-80 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
        <div className="mt-12 space-y-3 border-t border-[var(--color-border-default)] pt-8">
          <Skeleton className="h-6 w-32" />
          <Skeleton className="h-4 w-72 max-w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-[var(--color-border-default)] p-4 text-sm text-[var(--color-text-secondary)]">
        <AlertTriangle className="h-4 w-4 text-[var(--color-warning)]" />
        Couldn&apos;t load the file index for this repository.
      </div>
    );
  }

  return <FilesIndex files={data.files} languages={data.languages} fileHref={fileHref} />;
}
