"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
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
  const pathname = usePathname();
  // Reading `window.location.search` gave the URL the browser happened to be
  // showing rather than the one React rendered, and it is the pattern
  // packages/ui/README.md rules out for the shared components this hosts.
  const searchParams = useSearchParams();

  // Whether a server round trip could produce highlighted source at all. The
  // server declines to highlight when there is no coverage row and when the
  // covered-line set is empty, and both of those are visible from here — so a
  // click on Coverage for an un-ingested file must not pay for the aggregate
  // to be told "no" again. The two cases it cannot see (source over the size
  // cap, `/file-content` failing) are rare and cost one trip.
  const covered = data.coverage;
  const highlightPossible =
    !!covered && (covered.covered_line_count ?? covered.covered_lines.length) > 0;

  const onTabChange = (tab: FilePageTab) => {
    const sp = new URLSearchParams(searchParams.toString());
    if (tab === "overview") sp.delete("tab");
    else sp.set("tab", tab);
    const qs = sp.toString();
    const url = qs ? `${pathname}?${qs}` : pathname;

    // Coverage is the one tab whose body is server-rendered (shiki-highlighted
    // source), so it is the one tab that can still need the round trip. Every
    // other tab reads data already in `data`, and `router.replace` re-ran the
    // whole ~18-query aggregate plus the highlight to hand back a payload
    // identical to the one on screen. A shallow history entry keeps the tab
    // deep-linkable and reloadable without refetching anything.
    if (tab === "coverage" && highlightPossible && !coverageCodeHtml) {
      router.replace(url, { scroll: false });
      return;
    }
    window.history.replaceState(null, "", url);
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

  const fileName = data.file_path.split("/").pop() || data.file_path;
  const dir = data.file_path.slice(0, data.file_path.length - fileName.length).replace(/\/$/, "");

  return (
    <FilePage
      data={data}
      repoId={repoId}
      LinkComponent={Link}
      breadcrumb={[
        ...(dir ? [{ label: dir }] : []),
        { label: fileName },
      ]}
      docSlot={docSlot}
      coverageCodeHtml={coverageCodeHtml}
      wikiHref={wikiHref}
      initialTab={initialTab}
      onTabChange={onTabChange}
      onFindingStatusChange={onFindingStatusChange}
    />
  );
}
