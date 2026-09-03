"use client";

import useSWR from "swr";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { HealthFileDrawer } from "@repowise-dev/ui/health";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getPerformanceOpportunities,
  updateFindingStatus,
} from "@/lib/api/code-health";
import {
  getFileOpportunity,
  refactoringOpportunityHref,
} from "@/lib/api/file-opportunity";
import { useFileBreakdown } from "./use-file-breakdown";

/** Causes listed for one file. A file with more than this is its own queue. */
const FILE_CAUSE_LIMIT = 10;

export function HealthFileDrawerHost({
  repoId,
  filePath,
  onClose,
  lens,
}: {
  repoId: string;
  filePath: string | null;
  onClose: () => void;
  /** The surface the file was opened from; drives what the drawer leads with. */
  lens?: string;
}) {
  const router = useRouter();
  const { data, isLoading } = useFileBreakdown(repoId, filePath);
  const prefix = `/repos/${repoId}`;
  const filePageHref = filePath ? fileEntityPath(prefix, filePath) : undefined;

  // Scoped to the one file, by the server. Fetched only from the performance
  // lens, so opening a file from anywhere else costs the request it always did.
  const wantsCauses = lens === "performance" && filePath !== null;
  const { data: causes, isLoading: causesLoading } = useSWR(
    wantsCauses ? `file-performance-causes:${repoId}:${filePath}` : null,
    () =>
      getPerformanceOpportunities(repoId, {
        file_paths: [filePath as string],
        limit: FILE_CAUSE_LIMIT,
        // Every context. The reader opened this one file, so the question is
        // what was found in it, not whether it is production code.
        context: "all",
      }),
    { revalidateOnFocus: false },
  );

  // The file's one refactoring opportunity, so a finding in this drawer can
  // reach the plan the same analysis wrote for it. Unconditional, unlike the
  // performance causes above: this is one indexed lookup, and the link is the
  // drawer's only route out to the plan.
  const { data: opportunity } = useSWR(
    filePath ? `file-opportunity:${repoId}:${filePath}` : null,
    () => getFileOpportunity(repoId, filePath as string),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );

  return (
    <HealthFileDrawer
      open={filePath !== null}
      opportunity={opportunity}
      refactoringOpportunityHref={(id) => refactoringOpportunityHref(repoId, id)}
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
      lens={lens}
      performance={
        wantsCauses && causes ? { items: causes.items, total: causes.total } : null
      }
      performanceLoading={causesLoading}
      onOpportunitySelect={(opportunityId) =>
        router.push(`/repos/${repoId}/code-health?tab=performance&opportunity=${encodeURIComponent(opportunityId)}`)
      }
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
