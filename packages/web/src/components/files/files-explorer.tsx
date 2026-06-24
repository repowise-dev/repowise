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
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
        <Skeleton className="h-80 w-full" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-96 w-full" />
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
