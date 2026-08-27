"use client";

import { useState, type ReactNode } from "react";
import { ViewTabs } from "../shared/view-tabs";
import type { FilePageTab, FileTabDef } from "./file-page-tabs";

export interface FilePageProps {
  /**
   * The server-rendered header. A `ReactNode` rather than `data`, because the
   * header is a server component and this shell is the client boundary — see
   * `buildFilePanels`.
   */
  header?: ReactNode;
  /** The tab row, from `fileTabsFor(data)` on the server. */
  tabs: FileTabDef[];
  /** Server-rendered tab bodies, keyed by tab id. */
  panels: Partial<Record<FilePageTab, ReactNode>>;
  initialTab?: FilePageTab | undefined;
  onTabChange?: ((tab: FilePageTab) => void) | undefined;
}

/**
 * The file page's shell: the header, the one underline tab row, and the active
 * panel.
 *
 * It is deliberately thin. Everything with a figure or a table in it renders on
 * the server and arrives here as markup, so the only JavaScript this page ships
 * for six of its seven tabs is the tab row itself.
 *
 * `initialTab` seeds the state and nothing more. The effect that re-applied it
 * on every change fired a redundant second state update on top of the one
 * `handleTabChange` had just done, and only ever mattered because the host
 * re-rendered the server tree on a tab click. It no longer does — which is also
 * why a caller that turns its cross-file links into soft navigations must key
 * this component on the file path: React would otherwise preserve this state at
 * the same tree position and strand the active tab out of sync with the URL.
 */
export function FilePage({ header, tabs, panels, initialTab, onTabChange }: FilePageProps) {
  const seed: FilePageTab =
    initialTab && tabs.some((t) => t.id === initialTab) ? initialTab : "overview";
  const [activeTab, setActiveTab] = useState<FilePageTab>(seed);

  const handleTabChange = (tab: FilePageTab) => {
    setActiveTab(tab);
    onTabChange?.(tab);
  };

  const active = tabs.find((t) => t.id === activeTab) ?? tabs[0];

  return (
    <div className="flex flex-col gap-8">
      {header}
      <ViewTabs
        tabs={tabs.map((t) => ({
          id: t.id,
          label: t.label,
          ...(t.badge !== undefined ? { badge: t.badge } : {}),
        }))}
        value={activeTab}
        onValueChange={(id) => handleTabChange(id as FilePageTab)}
      >
        <div className="space-y-6">
          {active && (
            <p className="max-w-[75ch] text-sm leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
              {active.blurb}
            </p>
          )}
          {panels[activeTab]}
        </div>
      </ViewTabs>
    </div>
  );
}
