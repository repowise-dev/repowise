"use client";

import Link from "next/link";
import { usePathname, useParams } from "next/navigation";
import { BookOpen, BarChart3 } from "lucide-react";
import { cn } from "@/lib/utils/cn";

const tabs = [
  { label: "Explorer", segment: "", icon: BookOpen },
  { label: "Coverage", segment: "/coverage", icon: BarChart3 },
] as const;

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { id } = useParams<{ id: string }>();
  const basePath = `/repos/${id}/docs`;

  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0 border-b border-[var(--color-border-default)] px-4 sm:px-6">
        <nav className="flex gap-1 -mb-px" aria-label="Docs tabs">
          {tabs.map((tab) => {
            const href = `${basePath}${tab.segment}`;
            const isActive =
              tab.segment === ""
                ? pathname === basePath
                : pathname.startsWith(href);
            const Icon = tab.icon;
            return (
              <Link
                key={tab.segment}
                href={href}
                className={cn(
                  "inline-flex items-center gap-1.5 px-3 py-2.5 text-sm font-medium border-b-2 transition-colors",
                  isActive
                    ? "border-[var(--color-accent-primary)] text-[var(--color-accent-primary)]"
                    : "border-transparent text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] hover:border-[var(--color-border-default)]",
                )}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </div>
  );
}
