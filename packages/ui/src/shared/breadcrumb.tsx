"use client";

import { ChevronRight } from "lucide-react";

export interface BreadcrumbSegment {
  label: string;
  href?: string;
}

interface BreadcrumbProps {
  segments: BreadcrumbSegment[];
  LinkComponent?: React.ComponentType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
}

export function Breadcrumb({ segments, LinkComponent = "a" as never }: BreadcrumbProps) {
  if (segments.length === 0) return null;

  const Link = LinkComponent as React.ComponentType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;

  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-sm">
      {segments.map((segment, i) => {
        const isLast = i === segments.length - 1;
        return (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && (
              <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-tertiary)] shrink-0" />
            )}
            {isLast || !segment.href ? (
              <span className="text-[var(--color-text-primary)] font-medium truncate">
                {segment.label}
              </span>
            ) : (
              <Link
                href={segment.href}
                className="text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors truncate"
              >
                {segment.label}
              </Link>
            )}
          </span>
        );
      })}
    </nav>
  );
}
