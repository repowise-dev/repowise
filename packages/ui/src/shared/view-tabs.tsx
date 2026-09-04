"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface ViewTab {
  id: string;
  label: string;
  /** How much is behind this tab, so a clean one says so before it is clicked.
   *  A string carries a unit where the figure needs one ("61%"). */
  badge?: number | string;
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
  /** Id of a panel the host renders itself, for layouts where the panel cannot
   *  be a child (a full-height canvas that must be a flex sibling). The host
   *  puts `role="tabpanel"`, this id and `tabIndex={0}` on that container, and
   *  labels it `aria-labelledby={`${panelId}-tab-${activeTabId}`}` — tab ids are
   *  derived from this value so the host can name them without a callback. */
  panelId?: string;
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
  panelId: externalPanelId,
  className,
}: ViewTabsProps) {
  // Stable id base so each tab can be aria-labelled to the shared panel and
  // the panel can point back at the active tab.
  const baseId = React.useId();
  // Derived from the host's panelId when it owns the panel, so the two sides
  // agree on ids without threading a callback.
  const tabId = (id: string) => `${externalPanelId ?? baseId}-tab-${id}`;
  // Only claim a panel we can actually point at: an internal one when children
  // were given, otherwise the host's. With neither, `aria-controls` is dropped
  // rather than left dangling on an element that does not exist.
  const ownsPanel = children != null;
  const panelId = ownsPanel ? `${baseId}-panel` : externalPanelId;
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
              {...(panelId ? { "aria-controls": panelId } : {})}
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
              {/* Mono text, no ground. A filled pill on every tab is a ground
                  that does not respond to anything — the count is a figure, and
                  figures the machine produced are set in mono. */}
              {tab.badge !== undefined && (
                <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
                  {tab.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {ownsPanel && (
        <div id={panelId} role="tabpanel" aria-labelledby={tabId(value)} tabIndex={0}>
          {children}
        </div>
      )}
    </div>
  );
}
