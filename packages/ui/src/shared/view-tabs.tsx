"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface ViewTab {
  id: string;
  label: string;
  badge?: number;
  /** Optional leading icon. Lets a host carry a canonical icon for a tab (e.g.
   *  the Architecture/Knowledge-Graph surface) so both apps render the same
   *  glyph from one tab definition instead of hand-rolling their own toggle.
   *  Omit it and the tab is label-only. */
  icon?: React.ReactNode;
}

export interface ViewTabsProps {
  tabs: ViewTab[];
  value: string;
  onValueChange: (id: string) => void;
  /** The active panel, rendered below the tab row. */
  children?: React.ReactNode;
  className?: string;
}

/**
 * The single borderless-underline tab row. Pure presentation: callers own URL
 * sync via `value`/`onValueChange`. Replaces the segmented primitive usage and
 * bespoke `border-b` switchers.
 */
export function ViewTabs({
  tabs,
  value,
  onValueChange,
  children,
  className,
}: ViewTabsProps) {
  // Stable id base so each tab can be aria-labelled to the shared panel and
  // the panel can point back at the active tab.
  const baseId = React.useId();
  const tabId = (id: string) => `${baseId}-tab-${id}`;
  const panelId = `${baseId}-panel`;
  const tabRefs = React.useRef<Record<string, HTMLButtonElement | null>>({});

  // Keep the active tab in view even though the scrollbar is hidden — it can
  // otherwise scroll off-screen with no way to reveal it.
  React.useEffect(() => {
    tabRefs.current[value]?.scrollIntoView({ inline: "nearest", block: "nearest" });
  }, [value]);

  // Left/right arrow keys move selection (and focus) between tabs; Home/End
  // jump to the ends. Roving tabIndex keeps a single tab-stop for the row.
  const onKeyDown = (e: React.KeyboardEvent) => {
    const i = tabs.findIndex((t) => t.id === value);
    if (i < 0) return;
    let next = i;
    if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    else return;
    e.preventDefault();
    const nextTab = tabs[next];
    if (!nextTab) return;
    onValueChange(nextTab.id);
    tabRefs.current[nextTab.id]?.focus();
  };

  return (
    <div className={cn("space-y-4", className)}>
      <div
        role="tablist"
        onKeyDown={onKeyDown}
        className="flex items-center gap-4 overflow-x-auto border-b border-[var(--color-border-default)] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {tabs.map((tab) => {
          const active = tab.id === value;
          return (
            <button
              key={tab.id}
              ref={(el) => {
                tabRefs.current[tab.id] = el;
              }}
              id={tabId(tab.id)}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={panelId}
              tabIndex={active ? 0 : -1}
              onClick={() => onValueChange(tab.id)}
              className={cn(
                "inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 border-transparent px-1 pb-2 -mb-px text-sm font-medium ring-offset-[var(--color-bg-root)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] focus-visible:ring-offset-2",
                active
                  ? "border-[var(--color-accent-primary)] text-[var(--color-text-primary)]"
                  : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
              )}
            >
              {tab.icon != null && (
                <span className="inline-flex shrink-0 items-center" aria-hidden>
                  {tab.icon}
                </span>
              )}
              {tab.label}
              {typeof tab.badge === "number" && (
                <span className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-xs font-medium tabular-nums text-[var(--color-text-secondary)]">
                  {tab.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <div id={panelId} role="tabpanel" aria-labelledby={tabId(value)} tabIndex={0}>
        {children}
      </div>
    </div>
  );
}
