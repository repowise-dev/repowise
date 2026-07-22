"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Sparkles } from "lucide-react";
import {
  FreshnessTable,
  type FreshnessTableProps,
} from "@repowise-dev/ui/coverage/freshness-table";
import { AutoDocsBanner } from "@repowise-dev/ui/docs/auto-docs-banner";
import { isDeterministicPage } from "@repowise-dev/ui/lib/page-types";
import { Button } from "@repowise-dev/ui/ui/button";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { GenerationProgressWrapper } from "@/components/jobs/generation-progress-wrapper";
import { BulkGenerateConfirm } from "@/components/docs/bulk-generate-confirm";
import { useBulkGenerate } from "@/lib/hooks/use-bulk-generate";
import { regeneratePage } from "@/lib/api/pages";

export function FreshnessTableWithRegenerate({
  pages,
  repoId,
}: Pick<FreshnessTableProps, "pages"> & { repoId: string }) {
  const router = useRouter();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  // A single active job — a per-row regenerate or a bulk write — shown above
  // the table. On completion we revalidate the (server-rendered) page list.
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const bulk = useBulkGenerate(repoId);

  const templateCount = useMemo(
    () => pages.filter((p) => isDeterministicPage(p)).length,
    [pages],
  );
  const writtenCount = pages.length - templateCount;

  const jobId = activeJobId ?? bulk.jobId;
  // Only one job runs at a time (the server 409s a second), so refuse to launch
  // another while one is in flight rather than orphaning its progress slot.
  const busy = jobId != null;

  const toggleSelect = useCallback((pageId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(pageId)) next.delete(pageId);
      else next.add(pageId);
      return next;
    });
  }, []);

  const selectTemplates = useCallback((ids: string[]) => {
    setSelectedIds(new Set(ids));
  }, []);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  // Task 1.3: per-row regenerate now launches a tracked job with a toast and
  // error handling, instead of firing and forgetting.
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

  const selectedList = useMemo(() => [...selectedIds], [selectedIds]);

  function writeSelected() {
    if (busy || selectedList.length === 0) return;
    const n = selectedList.length;
    // Price on open (the confirm dialog), not live per checkbox — the estimate
    // endpoint is heavy. The toolbar shows the count only.
    bulk.begin(
      { kind: "page_ids", page_ids: selectedList },
      { label: `${n} selected ${n === 1 ? "page" : "pages"}`, defaultCascade: "none" },
    );
  }

  function writeAllTemplates() {
    if (busy || templateCount === 0) return;
    bulk.begin(
      { kind: "unwritten" },
      {
        label: "every page still generated from structure",
        defaultCascade: "none",
      },
    );
  }

  const toolbar =
    selectedIds.size > 0 ? (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-[var(--color-border-active)] bg-[var(--color-accent-muted)] px-3 py-2">
        <span className="text-sm text-[var(--color-text-primary)]">
          <span className="font-medium">{selectedIds.size}</span>{" "}
          {selectedIds.size === 1 ? "page" : "pages"} selected
        </span>
        <Button
          size="sm"
          onClick={writeSelected}
          disabled={busy}
          className="h-7 gap-1.5 text-xs"
        >
          <Sparkles className="h-3.5 w-3.5" />
          Write selected
        </Button>
      </div>
    ) : null;

  return (
    <div className="space-y-3">
      {templateCount > 0 && (
        <AutoDocsBanner
          templateCount={templateCount}
          writtenCount={writtenCount}
          onWriteAll={writeAllTemplates}
          className="rounded-md border"
        />
      )}

      {jobId && (
        <GenerationProgressWrapper
          jobId={jobId}
          onDone={() => {
            setActiveJobId(null);
            bulk.clearJob();
            clearSelection();
            router.refresh();
          }}
        />
      )}

      <FreshnessTable
        pages={pages}
        onRegenerate={handleRegenerate}
        selectable
        selectedIds={selectedIds}
        onToggleSelect={toggleSelect}
        onSelectTemplates={selectTemplates}
        onClearSelection={clearSelection}
        toolbar={toolbar}
      />

      <BulkGenerateConfirm flow={bulk} repoId={repoId} title="Write selected pages with AI" />
    </div>
  );
}
