"use client";

import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { FilePage, type FilePageTab, type FindingStatus } from "@repowise-dev/ui/files";
import { updateFindingStatus } from "@/lib/api/code-health";
import type { FileDetailResponse } from "@repowise-dev/types/files";

interface FilePageHostProps {
  repoId: string;
  data: FileDetailResponse;
  docSlot?: ReactNode;
  coverageCodeHtml?: string;
  wikiHref?: string;
  initialTab?: FilePageTab;
}

/** Client host for the file entity page: wires finding triage to the API
 *  (with toasts) and keeps the active tab in the URL for deep links. */
export function FilePageHost({
  repoId,
  data,
  docSlot,
  coverageCodeHtml,
  wikiHref,
  initialTab,
}: FilePageHostProps) {
  const router = useRouter();

  const onTabChange = (tab: FilePageTab) => {
    const sp = new URLSearchParams(window.location.search);
    if (tab === "doc") sp.delete("tab");
    else sp.set("tab", tab);
    const qs = sp.toString();
    router.replace(qs ? `?${qs}` : window.location.pathname, { scroll: false });
  };

  const onFindingStatusChange = async (findingId: string, status: FindingStatus) => {
    try {
      await updateFindingStatus(repoId, findingId, status);
      toast.success(`Finding marked ${status.replace("_", " ")}`);
    } catch (err) {
      toast.error("Couldn't update finding status");
      throw err;
    }
  };

  return (
    <FilePage
      data={data}
      repoId={repoId}
      docSlot={docSlot}
      coverageCodeHtml={coverageCodeHtml}
      wikiHref={wikiHref}
      initialTab={initialTab}
      onTabChange={onTabChange}
      onFindingStatusChange={onFindingStatusChange}
    />
  );
}
