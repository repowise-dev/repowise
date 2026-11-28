"use client";

/**
 * The shared zoom breadcrumb with this app's translated landmark name. The
 * component lives in the shared UI package so the editor extension's
 * Knowledge Graph panel renders the same trail.
 */

import type { ZoomNode } from "@repowise-dev/ui/zoom";
import { ZoomBreadcrumb as SharedZoomBreadcrumb } from "@repowise-dev/ui/zoom/zoom-breadcrumb";
import { useTranslations } from "next-intl";

interface ZoomBreadcrumbProps {
  chain: ZoomNode[];
  onCrumb: (id: string) => void;
}

export function ZoomBreadcrumb({ chain, onCrumb }: ZoomBreadcrumbProps) {
  const t = useTranslations("zoom");
  return <SharedZoomBreadcrumb chain={chain} onCrumb={onCrumb} label={t("breadcrumb.label")} />;
}
