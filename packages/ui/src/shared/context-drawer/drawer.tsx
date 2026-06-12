"use client";

import * as React from "react";
import { AdaptivePanel } from "../adaptive-panel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../ui/tabs";
import { ENTITY_KIND_LABEL } from "../entity/routes";
import type { EntityRef } from "../entity/types";
import { useContextDrawer } from "./store";

/**
 * Entity context drawer that any EntityLink can open: a right-side panel on
 * desktop, a swipe-dismissable bottom sheet on mobile (via AdaptivePanel).
 * Tab content is provided by the host application as render functions — this
 * keeps the drawer in `packages/ui` (presentational) while letting
 * `packages/web` supply data fetching for each tab.
 */
export type ContextDrawerTabId =
  | "overview"
  | "decisions"
  | "co_changes"
  | "blast_radius"
  | "owners"
  | "docs";

export interface ContextDrawerTab {
  id: ContextDrawerTabId;
  label: string;
  /** Renders the tab body for the currently open entity. */
  render: (entity: EntityRef) => React.ReactNode;
}

interface ContextDrawerProps {
  tabs?: ContextDrawerTab[];
  defaultTab?: ContextDrawerTabId;
}

const DEFAULT_TAB_ORDER: ContextDrawerTabId[] = [
  "overview",
  "decisions",
  "co_changes",
  "blast_radius",
  "owners",
  "docs",
];

const DEFAULT_TAB_LABELS: Record<ContextDrawerTabId, string> = {
  overview: "Overview",
  decisions: "Decisions",
  co_changes: "Co-changes",
  blast_radius: "Blast radius",
  owners: "Owners",
  docs: "Docs",
};

export function ContextDrawer({ tabs, defaultTab = "overview" }: ContextDrawerProps) {
  const { entity, close } = useContextDrawer();
  const open = entity !== null;

  // Reset to default tab when entity changes.
  const [activeTab, setActiveTab] = React.useState<ContextDrawerTabId>(defaultTab);
  React.useEffect(() => {
    if (entity) setActiveTab(defaultTab);
  }, [entity?.kind, entity?.id, entity?.repoId, defaultTab]);

  const orderedTabs = React.useMemo(() => {
    if (!tabs || tabs.length === 0) {
      return DEFAULT_TAB_ORDER.map<ContextDrawerTab>((id) => ({
        id,
        label: DEFAULT_TAB_LABELS[id],
        render: () => <PlaceholderBody label={DEFAULT_TAB_LABELS[id]} />,
      }));
    }
    return tabs;
  }, [tabs]);

  return (
    <AdaptivePanel
      open={open}
      onOpenChange={(o) => (!o ? close() : undefined)}
      eyebrow={entity ? ENTITY_KIND_LABEL[entity.kind] : undefined}
      title={entity ? entity.id : ""}
    >
      {entity && (
        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as ContextDrawerTabId)}
          className="flex min-h-0 flex-1 flex-col"
        >
          <TabsList className="flex-wrap gap-0.5 mx-3 mt-3 h-auto justify-start">
            {orderedTabs.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id} className="text-xs">
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            {orderedTabs.map((tab) => (
              <TabsContent key={tab.id} value={tab.id} className="mt-0">
                {tab.render(entity)}
              </TabsContent>
            ))}
          </div>
        </Tabs>
      )}
    </AdaptivePanel>
  );
}

function PlaceholderBody({ label }: { label: string }) {
  return (
    <div className="rounded-md border border-dashed border-[var(--color-border-default)] p-4 text-center text-xs text-[var(--color-text-tertiary)]">
      {label} content not yet wired.
    </div>
  );
}
