"use client";

import type { ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FilePage, type FilePageTab, type FileTabDef } from "@repowise-dev/ui/files";

interface FilePageHostProps {
  header: ReactNode;
  tabs: FileTabDef[];
  panels: Partial<Record<FilePageTab, ReactNode>>;
  initialTab?: FilePageTab | undefined;
  /**
   * Tabs whose body the current server render could not produce, so clicking
   * one has to go back for it. Empty when the page was fetched in full.
   */
  refetchTabs?: FilePageTab[];
}

/** Client host for the file entity page: keeps the active tab in the URL for
 *  deep links without re-running the aggregate for the tabs that do not need
 *  it. Everything it renders arrived as server markup. */
export function FilePageHost({
  header,
  tabs,
  panels,
  initialTab,
  refetchTabs = [],
}: FilePageHostProps) {
  const router = useRouter();
  const pathname = usePathname();
  // Reading `window.location.search` gave the URL the browser happened to be
  // showing rather than the one React rendered, and it is the pattern
  // packages/ui/README.md rules out for the shared components this hosts.
  const searchParams = useSearchParams();

  const onTabChange = (tab: FilePageTab) => {
    const sp = new URLSearchParams(searchParams.toString());
    if (tab === "overview") sp.delete("tab");
    else sp.set("tab", tab);
    const qs = sp.toString();
    const url = qs ? `${pathname}?${qs}` : pathname;

    // The three heavy tabs — Documentation, Health and Coverage — are the only
    // ones whose bodies read a payload the slim fetch drops. When the page was
    // served slim, the first click into one of them needs the round trip that
    // produces it; every other tab reads data already on screen, and
    // `router.replace` re-ran the whole aggregate to hand back a payload
    // identical to the one being displayed. A shallow history entry keeps the
    // tab deep-linkable and reloadable without refetching anything.
    if (refetchTabs.includes(tab)) {
      router.replace(url, { scroll: false });
      return;
    }
    window.history.replaceState(null, "", url);
  };

  return (
    <FilePage
      header={header}
      tabs={tabs}
      panels={panels}
      initialTab={initialTab}
      onTabChange={onTabChange}
    />
  );
}
