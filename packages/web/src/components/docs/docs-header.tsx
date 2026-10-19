"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import useSWR from "swr";
import {
  DocsHeader as DocsHeaderShell,
  type DocsHeaderTab,
} from "@repowise-dev/ui/docs/docs-header";
import type { DocDriftResponse } from "@repowise-dev/types/doc-drift";
import { getDocDrift } from "@/lib/api/doc-drift";

// "Doc freshness", not "Coverage" — coverage means test coverage everywhere
// else in the app; this tab is about documentation staleness.
const TABS = [
  { labelKey: "explorer", segment: "", icon: "explorer" as const },
  { labelKey: "freshness", segment: "/coverage", icon: "freshness" as const },
  { labelKey: "drift", segment: "/drift", icon: "drift" as const },
];

/**
 * Web wrapper: resolves the route-aware tab list and injects Next's ``Link``
 * into the presentational ``DocsHeader`` ui shell.
 */
export function DocsHeader({ children }: { children?: React.ReactNode }) {
  const t = useTranslations("views.docsTabs");
  const pathname = usePathname();
  const { id } = useParams<{ id: string }>();
  const basePath = `/repos/${id}/docs`;

  // `limit: 1`: the summary counts before the cap, so one row carries the
  // badge. No badge when the drift pass has not run: the response then
  // carries a cause rather than a summary, and a 0 would read as clean.
  const { data: drift } = useSWR<DocDriftResponse>(
    `doc-drift-badge:${id}`,
    () => getDocDrift(id, { limit: 1 }),
    { revalidateOnFocus: false },
  );
  const driftCount = drift?.summary?.findings_total;

  const tabs: DocsHeaderTab[] = TABS.map((tab) => {
    const href = `${basePath}${tab.segment}`;
    return {
      label: t(tab.labelKey),
      href,
      icon: tab.icon,
      isActive:
        tab.segment === "" ? pathname === basePath : pathname.startsWith(href),
      ...(tab.icon === "drift" && driftCount !== undefined ? { badge: driftCount } : {}),
    };
  });

  return (
    <DocsHeaderShell tabs={tabs} LinkComponent={Link}>
      {children}
    </DocsHeaderShell>
  );
}
