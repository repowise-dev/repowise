"use client";

import * as React from "react";
import { ArrowLeft } from "lucide-react";

export interface ModuleDetailShellProps {
  /** Href for the "← All modules" back link. */
  backHref: string;
  LinkComponent?: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  children?: React.ReactNode;
}

/**
 * The ui-owned chrome for the module detail route: the `← All modules` back
 * link plus the centred wide shell. Lives in ui so the hosted app picks up
 * the layout on a package bump; the web route only injects the href + Link.
 */
export function ModuleDetailShell({
  backHref,
  LinkComponent = "a",
  children,
}: ModuleDetailShellProps) {
  const Link = LinkComponent;
  return (
    <div className="mx-auto w-full max-w-[1600px] space-y-4 p-4 sm:p-6">
      <Link
        href={backHref}
        className="inline-flex items-center gap-1 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
      >
        <ArrowLeft className="h-3 w-3" /> All modules
      </Link>
      {children}
    </div>
  );
}
