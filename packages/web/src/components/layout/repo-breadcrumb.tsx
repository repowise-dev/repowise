"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Breadcrumb } from "@repowise-dev/ui/shared/breadcrumb";
import type { BreadcrumbSegment } from "@repowise-dev/ui/shared/breadcrumb";
import { DocsModeBadge, type DocsMode } from "@repowise-dev/ui/docs/docs-mode-badge";
import { getRepoBreadcrumbSegmentLabel } from "./repo-breadcrumb-label";

export function RepoBreadcrumb({
  repoName,
  docsMode = "none",
}: {
  repoName: string;
  /** Provenance of the repo's wiki, from the repos API `docs_mode`. */
  docsMode?: DocsMode;
}) {
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

  const showCrumbs = segments.length > 2;
  const showBadge = docsMode !== "none";
  // Nothing to show at the repo root with no docs — stay out of the way.
  if (!showCrumbs && !showBadge) return null;

  return (
    <div className="flex items-center justify-between gap-3 px-4 sm:px-6 py-2 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
      {showCrumbs ? <Breadcrumb segments={segments} LinkComponent={Link} /> : <span />}
      {showBadge && <DocsModeBadge mode={docsMode} />}
    </div>
  );
}
