"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  FreshnessTable,
  type FreshnessTableProps,
} from "@repowise-dev/ui/coverage/freshness-table";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { GenerationProgressWrapper } from "@/components/jobs/generation-progress-wrapper";
import { regeneratePage } from "@/lib/api/pages";

export function FreshnessTableWithRegenerate({
  pages,
  repoId,
}: Pick<FreshnessTableProps, "pages"> & { repoId: string }) {
  const router = useRouter();
  // A single active job (a per-row regenerate) shown above the table. On
  // completion we revalidate the (server-rendered) page list.
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const busy = activeJobId != null;

  // Per-row regenerate launches a tracked job with a toast and error handling.
  const handleRegenerate = useCallback(
    async (pageId: string) => {
      if (busy) {
        toast.info("A generation job is already running. Wait for it to finish.");
        return;
      }
      try {
        const res = await regeneratePage(pageId, { cascade: "none" });
        setActiveJobId(res.job_id);
        toast.info("Regeneration started");
      } catch (e) {
        toast.error("Couldn't start regeneration", { description: toFriendlyMessage(e) });
      }
    },
    [busy],
  );

  return (
    <div className="space-y-3">
      {activeJobId && (
        <GenerationProgressWrapper
          jobId={activeJobId}
          onDone={() => {
            setActiveJobId(null);
            router.refresh();
          }}
        />
      )}

      <FreshnessTable pages={pages} onRegenerate={handleRegenerate} />
    </div>
  );
}
