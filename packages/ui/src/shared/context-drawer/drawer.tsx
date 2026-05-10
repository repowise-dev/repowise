"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "../../lib/cn";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../ui/tabs";
import { ENTITY_KIND_LABEL } from "../entity/routes";
import type { EntityRef } from "../entity/types";
import { useContextDrawer } from "./store";

/**
 * Right-side slide-in drawer that any EntityLink can open. Tab content is
 * provided by the host application as render functions — this keeps the
 * drawer in `packages/ui` (presentational) while letting `packages/web`
 * supply data fetching for each tab.
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
    <DialogPrimitive.Root open={open} onOpenChange={(o) => (!o ? close() : undefined)}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[1px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          className={cn(
            "fixed right-0 top-0 z-50 flex h-full w-full max-w-[520px] flex-col",
            "border-l border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shadow-2xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right",
          )}
          aria-describedby={undefined}
        >
          {entity && (
            <>
              <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border-default)] px-4 py-3">
                <div className="min-w-0">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    {ENTITY_KIND_LABEL[entity.kind]}
                  </p>
                  <DialogPrimitive.Title className="mt-0.5 break-all font-mono text-[12px] leading-snug text-[var(--color-text-primary)]">
                    {entity.id}
                  </DialogPrimitive.Title>
                </div>
                <DialogPrimitive.Close
                  aria-label="Close drawer"
                  className="shrink-0 rounded-md p-1.5 text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
                >
                  <X className="h-4 w-4" />
                </DialogPrimitive.Close>
              </header>

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
            </>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function PlaceholderBody({ label }: { label: string }) {
  return (
    <div className="rounded-md border border-dashed border-[var(--color-border-default)] p-4 text-center text-xs text-[var(--color-text-tertiary)]">
      {label} content not yet wired.
    </div>
  );
}
