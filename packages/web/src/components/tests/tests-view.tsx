"use client";

/**
 * Tests host — binds the shared {@link CoverageView} to web's `/api` client
 * and `/repos/:id` routing, and passes its copy in the reader's language. The
 * composition lives in `@repowise-dev/ui/health`; this file only injects the
 * app-specific data fetchers, navigation and strings.
 */

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  CoverageView,
  type CodeHealthAdapter,
  type CoverageViewCopy,
} from "@repowise-dev/ui/health";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import {
  getHealthCoverage,
  getTestsReaching,
  getHealthOverview,
  getHealthWorkQueue,
  listHealthFindings,
  updateFindingStatus,
} from "@/lib/api/code-health";

export function TestsView({ repoId: id }: { repoId: string }) {
  const router = useRouter();
  const t = useTranslations("views.tests");
  const prefix = `/repos/${id}`;

  const adapter: CodeHealthAdapter = {
    cacheKey: id,
    getOverview: (limit) => getHealthOverview(id, limit),
    listFindings: (opts) => listHealthFindings(id, opts),
    getHealthWorkQueue: (opts) => getHealthWorkQueue(id, opts),
    updateFindingStatus: (findingId, status) =>
      updateFindingStatus(id, findingId, status),
    getCoverage: (opts) => getHealthCoverage(id, opts),
    getTestsReaching: (path) => getTestsReaching(id, path),
    fileHref: (path) => fileEntityPath(prefix, path),
    navigate: (href) => router.push(href),
    renderFileDrawer: () => null,
  };

  const copy = useMemo<CoverageViewCopy>(
    () => ({
      promptLabel: t("promptAction"),
      batchPromptLabel: (count) => t("batchPromptAction", { count }),
      cleanTitle: (files) => t("cleanTitle", { files: formatNumber(files) }),
      cleanDescription: t("cleanBody"),
    }),
    [t],
  );

  return <CoverageView adapter={adapter} copy={copy} />;
}
