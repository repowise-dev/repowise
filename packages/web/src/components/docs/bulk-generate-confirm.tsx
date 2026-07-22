"use client";

import { GenerateConfirmDialog } from "@repowise-dev/ui/wiki/regenerate-button";
import { formatEstimateCost } from "@/lib/generate-format";
import type { useBulkGenerate } from "@/lib/hooks/use-bulk-generate";

/**
 * Maps a `useBulkGenerate` flow onto the shared, presentational
 * `GenerateConfirmDialog` (selection-scoped cascade wording). Kept tiny so the
 * quick action, banner, and coverage toolbar reuse one mapping.
 */
export function BulkGenerateConfirm({
  flow,
  repoId,
  title,
}: {
  flow: ReturnType<typeof useBulkGenerate>;
  repoId: string;
  /** Dialog title, e.g. "Write documentation with AI". */
  title?: string;
}) {
  const { estimate, noProvider, label } = flow;
  const pages = estimate?.total_pages ?? 0;

  return (
    <GenerateConfirmDialog
      open={flow.confirmOpen}
      onOpenChange={flow.setConfirmOpen}
      mode="write"
      title={title ?? "Write documentation with AI"}
      cascadeScope="selection"
      description={
        <>
          Write{" "}
          <span className="font-medium text-[var(--color-text-primary)]">
            {label ?? "the selected pages"}
          </span>{" "}
          with your configured model.
        </>
      }
      confirmLabel={pages > 0 ? `Write ${pages} ${pages === 1 ? "page" : "pages"}` : "Write with AI"}
      cascade={flow.cascade}
      onCascadeChange={flow.changeCascade}
      estimate={
        estimate && !noProvider
          ? {
              totalPages: estimate.total_pages,
              costText: formatEstimateCost(estimate.estimate),
              staleCount: estimate.pages_to_mark_stale,
            }
          : null
      }
      estimateLoading={flow.estimateLoading}
      estimateError={flow.estimateError}
      noProvider={noProvider}
      settingsHref={`/repos/${repoId}/settings#provider`}
      onConfirm={flow.confirm}
      launching={flow.launching}
    />
  );
}
