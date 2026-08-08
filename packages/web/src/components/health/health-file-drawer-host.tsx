"use client";

import { toast } from "sonner";
import { HealthFileDrawer } from "@repowise-dev/ui/health";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { updateFindingStatus } from "@/lib/api/code-health";
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
  const prefix = `/repos/${repoId}`;
  const filePageHref = filePath ? fileEntityPath(prefix, filePath) : undefined;

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
      trend={data?.trend ?? null}
      signals={data?.signals ?? null}
      permalinkHref={filePageHref ? `${filePageHref}?tab=health` : undefined}
      fileViewHref={filePageHref}
      fileViewHrefFor={
        // Carry the line through. This used to discard its argument, which was
        // survivable while the link only appeared next to a function name; now
        // that file-level markers render their own line, dropping it would make
        // 34 visually distinct "line N" links resolve to one identical URL.
        filePageHref
          ? (lineStart) => `${filePageHref}?tab=health#L${lineStart}`
          : undefined
      }
      onPartnerHref={(path) => fileEntityPath(prefix, path)}
      onFindingStatusChange={async (findingId, status) => {
        try {
          await updateFindingStatus(
            repoId,
            findingId,
            status as Parameters<typeof updateFindingStatus>[2],
          );
          toast.success(`Finding marked ${status.replace("_", " ")}`);
        } catch (err) {
          toast.error("Couldn't update finding status");
          throw err;
        }
      }}
    />
  );
}
