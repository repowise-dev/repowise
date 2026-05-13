"use client";

/**
 * GraphContextDrawer — single tabbed right-side surface for the selected node.
 *
 * Replaces the previously parallel right-panels (doc / inspection / community
 * / ego) with one coordinated drawer keyed by ``selectedNodeId``. The drawer
 * is intentionally presentational and shape-agnostic — callers pass tab bodies
 * as ``render`` functions, which lets `packages/web` wire in its own data hooks
 * and lets the hosted frontend plug in different implementations.
 *
 * Design intent:
 *   - One source of truth for which node is selected
 *   - Tab order is stable; tabs gracefully no-op when their data is unavailable
 *   - Header surfaces signal badges via the shared `NodeBadges` component so
 *     the user sees doc / hotspot / dead / decision state at a glance
 *   - Actions tab carries deep links to the rest of the product (wiki, git,
 *     risk, decisions, file viewer) — wired by the caller
 */

import * as React from "react";
import { X } from "lucide-react";
import { cn } from "../lib/cn";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";
import { NodeBadges, type NodeSignalInput } from "./node-badges";

export type GraphDrawerTabId =
  | "overview"
  | "docs"
  | "git"
  | "symbols"
  | "neighborhood"
  | "actions";

export interface GraphDrawerTab {
  id: GraphDrawerTabId;
  label: string;
  /** Render the tab body. May return null to indicate "nothing to show". */
  render: () => React.ReactNode;
  /** Hide the tab trigger entirely (e.g. symbols tab for non-symbol nodes). */
  hidden?: boolean;
  /** Disable the tab trigger but keep it visible (e.g. loading state). */
  disabled?: boolean;
}

export interface GraphContextDrawerProps {
  /** Currently selected node id; ``null`` collapses the drawer. */
  nodeId: string | null;
  /** Display title — usually the file basename. */
  title?: string;
  /** Optional path/subtitle shown under the title. */
  subtitle?: string;
  /** Signals to render as badges in the header row. */
  signals?: NodeSignalInput;
  tabs: GraphDrawerTab[];
  defaultTab?: GraphDrawerTabId;
  onClose: () => void;
  /** Width in pixels; defaults to 380. */
  width?: number;
  className?: string;
}

const DEFAULT_TAB_ORDER: GraphDrawerTabId[] = [
  "overview",
  "docs",
  "git",
  "symbols",
  "neighborhood",
  "actions",
];

export function GraphContextDrawer({
  nodeId,
  title,
  subtitle,
  signals,
  tabs,
  defaultTab = "overview",
  onClose,
  width = 380,
  className,
}: GraphContextDrawerProps) {
  const visibleTabs = React.useMemo(() => {
    const byId = new Map(tabs.filter((t) => !t.hidden).map((t) => [t.id, t]));
    // Stable canonical order; unknown tab ids appended after.
    const ordered: GraphDrawerTab[] = [];
    for (const id of DEFAULT_TAB_ORDER) {
      const t = byId.get(id);
      if (t) {
        ordered.push(t);
        byId.delete(id);
      }
    }
    return ordered.concat(Array.from(byId.values()));
  }, [tabs]);

  const initialTab = React.useMemo(() => {
    if (visibleTabs.some((t) => t.id === defaultTab && !t.disabled)) return defaultTab;
    return visibleTabs.find((t) => !t.disabled)?.id ?? defaultTab;
  }, [visibleTabs, defaultTab]);

  const [active, setActive] = React.useState<GraphDrawerTabId>(initialTab);
  React.useEffect(() => {
    setActive(initialTab);
  }, [nodeId, initialTab]);

  // Close on Escape so the drawer matches the rest of the graph UI shortcuts.
  React.useEffect(() => {
    if (!nodeId) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [nodeId, onClose]);

  if (!nodeId) return null;

  return (
    <aside
      className={cn(
        "relative flex h-full flex-col border-l border-[var(--color-border-default)] bg-[var(--color-bg-surface)]",
        "shadow-xl shadow-black/10",
        className,
      )}
      style={{ width }}
      role="complementary"
      aria-label="Selected node context"
    >
      <header className="flex items-start gap-2 border-b border-[var(--color-border-default)] p-3">
        <div className="min-w-0 flex-1">
          {title && (
            <p className="truncate font-mono text-[12px] font-semibold text-[var(--color-text-primary)]">
              {title}
            </p>
          )}
          {subtitle && (
            <p className="truncate text-[10px] text-[var(--color-text-tertiary)]">
              {subtitle}
            </p>
          )}
          {signals && (
            <NodeBadges signals={signals} className="mt-1.5" />
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close panel"
          className="shrink-0 rounded p-1 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-inset)] hover:text-[var(--color-text-primary)]"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <Tabs
        value={active}
        onValueChange={(v) => setActive(v as GraphDrawerTabId)}
        className="flex min-h-0 flex-1 flex-col"
      >
        <TabsList className="mx-3 mt-2 h-auto w-auto flex-wrap justify-start bg-transparent p-0 gap-1">
          {visibleTabs.map((tab) => (
            <TabsTrigger
              key={tab.id}
              value={tab.id}
              disabled={tab.disabled}
              className="rounded-md px-2 py-1 text-[11px] font-medium data-[state=active]:bg-[var(--color-bg-inset)] data-[state=active]:text-[var(--color-text-primary)]"
            >
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <div className="min-h-0 flex-1 overflow-auto">
          {visibleTabs.map((tab) => (
            <TabsContent
              key={tab.id}
              value={tab.id}
              className="m-0 h-full p-3 outline-none"
            >
              {tab.render()}
            </TabsContent>
          ))}
        </div>
      </Tabs>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Actions tab — reusable deep-link list.
// ---------------------------------------------------------------------------

export interface GraphDrawerAction {
  id: string;
  label: string;
  description?: string;
  icon?: React.ReactNode;
  href?: string;
  onClick?: () => void;
  disabled?: boolean;
}

export interface GraphDrawerActionsProps {
  actions: GraphDrawerAction[];
}

export function GraphDrawerActions({ actions }: GraphDrawerActionsProps) {
  if (actions.length === 0) {
    return (
      <p className="text-[11px] text-[var(--color-text-tertiary)]">
        No actions available for this node.
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-1.5">
      {actions.map((a) => {
        const inner = (
          <span className="flex items-start gap-2">
            {a.icon && <span className="mt-0.5 shrink-0">{a.icon}</span>}
            <span className="min-w-0 flex-1">
              <span className="block text-[12px] font-medium text-[var(--color-text-primary)]">
                {a.label}
              </span>
              {a.description && (
                <span className="block text-[11px] text-[var(--color-text-tertiary)]">
                  {a.description}
                </span>
              )}
            </span>
          </span>
        );
        const cls = cn(
          "block rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-3 py-2 text-left transition-colors",
          a.disabled
            ? "cursor-not-allowed opacity-50"
            : "hover:border-[var(--color-accent-graph)]/40 hover:bg-[var(--color-bg-surface)]",
        );
        return (
          <li key={a.id}>
            {a.href && !a.disabled ? (
              <a href={a.href} className={cls}>
                {inner}
              </a>
            ) : (
              <button
                type="button"
                onClick={a.onClick}
                disabled={a.disabled}
                className={cn(cls, "w-full")}
              >
                {inner}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
