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
 * Single compact chrome row for the documentation surface: title, the
 * Explorer / Doc-freshness view switch as a segmented control, and a
 * right-aligned slot for page-specific actions. Pure presentation — the host
 * resolves tab hrefs/active state and injects a router-aware ``LinkComponent``.
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
    <div className="shrink-0 flex h-12 items-center gap-3 border-b border-[var(--color-border-default)] px-4 sm:px-6">
      <h1 className="text-sm font-semibold text-[var(--color-text-primary)]">
        Documentation
      </h1>

      <nav
        className="flex items-center rounded-lg bg-[var(--color-bg-elevated)] p-0.5"
        aria-label="Docs views"
      >
        {tabs.map((tab) => {
          const Icon = ICONS[tab.icon];
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={tab.isActive ? "page" : undefined}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                tab.isActive
                  ? "bg-[var(--color-bg-surface)] text-[var(--color-text-primary)] shadow-sm"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {tab.label}
            </Link>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-2">{children}</div>
    </div>
  );
}
