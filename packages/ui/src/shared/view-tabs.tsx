"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface ViewTab {
  id: string;
  label: string;
  badge?: number;
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
  return (
    <div className={cn("space-y-4", className)}>
      <div
        role="tablist"
        className="flex items-center gap-4 overflow-x-auto border-b border-[var(--color-border-default)] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {tabs.map((tab) => {
          const active = tab.id === value;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onValueChange(tab.id)}
              className={cn(
                "inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 border-transparent px-1 pb-2 -mb-px text-sm font-medium ring-offset-[var(--color-bg-root)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] focus-visible:ring-offset-2",
                active
                  ? "border-[var(--color-accent-primary)] text-[var(--color-text-primary)]"
                  : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
              )}
            >
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
      {children}
    </div>
  );
}
