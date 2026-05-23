"use client";

import { HealthFileDrawer } from "@repowise-dev/ui/health";
import { useFileBreakdown } from "./use-file-breakdown";

export function HealthFileDrawerHost({
  repoId,
  filePath,
  onClose,
}: {
  repoId: string;
  filePath: string | null;
  onClose: () => void;
}) {
  const { data, isLoading } = useFileBreakdown(repoId, filePath);
  return (
    <HealthFileDrawer
      open={filePath !== null}
      onClose={onClose}
      loading={isLoading}
      metric={data?.metric ?? null}
      breakdown={
        data
          ? {
              score: data.breakdown.score,
              total_deduction: data.breakdown.total_deduction,
              categories: data.breakdown.categories,
            }
          : null
      }
      findings={data?.findings ?? []}
      suggestions={data?.suggestions ?? {}}
      fileViewHref={filePath ? `/repos/${repoId}/files?path=${encodeURIComponent(filePath)}` : undefined}
      fileViewHrefFor={
        filePath
          ? (line: number) =>
              `/repos/${repoId}/files?path=${encodeURIComponent(filePath)}#L${line}`
          : undefined
      }
    />
  );
}
