"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import {
  DocsHeader as DocsHeaderShell,
  type DocsHeaderTab,
} from "@repowise-dev/ui/docs/docs-header";

// "Doc freshness", not "Coverage" — coverage means test coverage everywhere
// else in the app; this tab is about documentation staleness.
const TABS = [
  { label: "Explorer", segment: "", icon: "explorer" as const },
  { label: "Doc freshness", segment: "/coverage", icon: "freshness" as const },
];

/**
 * Web wrapper: resolves the route-aware tab list and injects Next's ``Link``
 * into the presentational ``DocsHeader`` ui shell.
 */
export function DocsHeader({ children }: { children?: React.ReactNode }) {
  const pathname = usePathname();
  const { id } = useParams<{ id: string }>();
  const basePath = `/repos/${id}/docs`;

  const tabs: DocsHeaderTab[] = TABS.map((tab) => {
    const href = `${basePath}${tab.segment}`;
    return {
      label: tab.label,
      href,
      icon: tab.icon,
      isActive:
        tab.segment === "" ? pathname === basePath : pathname.startsWith(href),
    };
  });

  return (
    <DocsHeaderShell tabs={tabs} LinkComponent={Link}>
      {children}
    </DocsHeaderShell>
  );
}
