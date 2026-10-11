"use client";

import { BookOpen, BarChart3 } from "lucide-react";
import { cn } from "../lib/cn";
import type { ReaderLinkComponent } from "./docs-reader";

export interface DocsHeaderTab {
  label: string;
  href: string;
  isActive: boolean;
  icon: "explorer" | "freshness";
}

const ICONS = {
  explorer: BookOpen,
  freshness: BarChart3,
} as const;

/**
 * Single compact chrome row for the documentation surface: the Explorer /
 * Doc-freshness view switch as a segmented control, and a right-aligned slot
 * for page-specific actions. Pure presentation — the host resolves tab
 * hrefs/active state and injects a router-aware ``LinkComponent``.
 *
 * The row sits beside the pages tree rather than above it, so the tree keeps
 * the full height of the window and this bar spans only the surface it acts
 * on. The "Documentation" heading it used to show is kept for document
 * structure but no longer rendered: the route is /docs, the tab strip says
 * Explorer, and on the reader the page's own title is the h1 directly beneath.
 */
export function DocsHeader({
  tabs,
  LinkComponent,
  children,
}: {
  tabs: DocsHeaderTab[];
  LinkComponent: ReaderLinkComponent;
  children?: React.ReactNode;
}) {
  const Link = LinkComponent;
  return (
    <div className="shrink-0 flex h-12 items-center gap-2 border-b border-[var(--color-border-default)] px-3 sm:gap-3 sm:px-6">
      <h1 className="sr-only">Documentation</h1>

      <nav
        className="flex shrink-0 items-center rounded-lg bg-[var(--color-bg-elevated)] p-0.5"
        aria-label="Docs views"
      >
        {tabs.map((tab) => {
          const Icon = ICONS[tab.icon];
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={tab.isActive ? "page" : undefined}
              title={tab.label}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium whitespace-nowrap transition-colors sm:px-2.5",
                tab.isActive
                  ? "bg-[var(--color-bg-surface)] text-[var(--color-text-primary)] shadow-sm"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]",
              )}
            >
              <Icon className="h-3.5 w-3.5 shrink-0" />
              {/* On a phone the two icons carry the switch on their own. The
                  labels are the first thing to go: they are the widest element
                  in the row and the least load-bearing, since the active tab is
                  already marked by its filled ground. */}
              <span className="hidden sm:inline">{tab.label}</span>
            </Link>
          );
        })}
      </nav>

      {/* Actions scroll rather than wrap or squeeze the switch. */}
      <div className="ml-auto flex min-w-0 items-center gap-2 overflow-x-auto">
        {children}
      </div>
    </div>
  );
}
