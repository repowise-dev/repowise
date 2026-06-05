"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Breadcrumb } from "@repowise-dev/ui/shared/breadcrumb";
import type { BreadcrumbSegment } from "@repowise-dev/ui/shared/breadcrumb";
import { getRepoBreadcrumbSegmentLabel } from "./repo-breadcrumb-label";

export function RepoBreadcrumb({ repoName }: { repoName: string }) {
  const pathname = usePathname();
  const match = pathname.match(/^\/repos\/([^/]+)(.*)/);
  if (!match) return null;

  const repoId = match[1];
  const rest = match[2]?.replace(/^\//, "").split("/").filter(Boolean) ?? [];

  const segments: BreadcrumbSegment[] = [
    { label: "Dashboard", href: "/" },
    { label: repoName, href: `/repos/${repoId}` },
  ];

  let currentPath = `/repos/${repoId}`;
  for (const seg of rest) {
    currentPath += `/${seg}`;
    segments.push({
      label: getRepoBreadcrumbSegmentLabel(seg),
      href: currentPath,
    });
  }

  if (segments.length <= 2) return null;

  return (
    <div className="px-4 sm:px-6 py-2 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
      <Breadcrumb segments={segments} LinkComponent={Link} />
    </div>
  );
}
