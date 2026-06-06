"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { BookOpen, BarChart3 } from "lucide-react";
import { cn } from "@/lib/utils/cn";

const TABS = [
  { label: "Explorer", segment: "", icon: BookOpen },
  { label: "Coverage", segment: "/coverage", icon: BarChart3 },
] as const;

/**
 * Single compact chrome row for the Docs surface: title, the
 * Explorer / Coverage view switch as a segmented control, and a right-aligned
 * slot for page-specific actions. Replaces the old two-row stack
 * (underline tabs row + title/actions row).
 */
export function DocsHeader({ children }: { children?: React.ReactNode }) {
  const pathname = usePathname();
  const { id } = useParams<{ id: string }>();
  const basePath = `/repos/${id}/docs`;

  return (
    <div className="shrink-0 flex h-12 items-center gap-3 border-b border-[var(--color-border-default)] px-4 sm:px-6">
      <h1 className="text-sm font-semibold text-[var(--color-text-primary)]">
        Documentation
      </h1>

      <nav
        className="flex items-center rounded-lg bg-[var(--color-bg-elevated)] p-0.5"
        aria-label="Docs views"
      >
        {TABS.map((tab) => {
          const href = `${basePath}${tab.segment}`;
          const isActive =
            tab.segment === "" ? pathname === basePath : pathname.startsWith(href);
          const Icon = tab.icon;
          return (
            <Link
              key={tab.segment}
              href={href}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                isActive
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
