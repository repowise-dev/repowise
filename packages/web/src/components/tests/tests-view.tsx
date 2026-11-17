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
import useSWR from "swr";
import {
  CoverageView,
  type CodeHealthAdapter,
  type CoverageViewCopy,
} from "@repowise-dev/ui/health";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { getOverviewSummary } from "@/lib/api/overview";
import {
  getHealthCoverage,
  getTestsReaching,
  getHealthOverview,
  getHealthWorkQueue,
  listHealthFindings,
  updateFindingStatus,
} from "@/lib/api/code-health";

/**
 * Resolves the idiomatic test + coverage command pair for the repository's
 * primary language. Falls back to undefined (which defaults to language-neutral
 * `repowise coverage add`).
 */
export function coverageCommandForLanguages(
  languages: Array<{ language: string }> | undefined,
): string | undefined {
  const primary = languages?.[0]?.language?.trim().toLowerCase();
  switch (primary) {
    case "python":
      return `pytest --cov --cov-report=lcov\nrepowise coverage add coverage.lcov`;
    case "go":
      return `go test -coverprofile=coverage.out ./...\nrepowise coverage add coverage.out`;
    case "typescript":
    case "javascript":
      return `npm test -- --coverage\nrepowise coverage add coverage/lcov.info`;
    case "rust":
      return `cargo llvm-cov --lcov --output-path coverage/lcov.info\nrepowise coverage add coverage/lcov.info`;
    case "java":
    case "kotlin":
      return `mvn test jacoco:report\nrepowise coverage add target/site/jacoco/jacoco.xml`;
    default:
      return undefined;
  }
}

export function TestsView({ repoId: id }: { repoId: string }) {
  const router = useRouter();
  const t = useTranslations("views.tests");
  const prefix = `/repos/${id}`;

  const { data: summary } = useSWR(
    id ? `overview-summary:${id}` : null,
    () => getOverviewSummary(id),
    { revalidateOnFocus: false },
  );

  const coverageCommand = useMemo(
    () => coverageCommandForLanguages(summary?.languages),
    [summary?.languages],
  );

  const adapter: CodeHealthAdapter = useMemo(
    () => ({
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
    }),
    [id, prefix, router],
  );

  const copy = useMemo<CoverageViewCopy>(
    () => ({
      promptLabel: t("promptAction"),
      batchPromptLabel: (count) => t("batchPromptAction", { count }),
      cleanTitle: (files) => t("cleanTitle", { files: formatNumber(files) }),
      cleanDescription: t("cleanBody"),
      unreachedTitle: t("unreachedTitle"),
      unavailableTitle: t("unavailableTitle"),
      unavailableBody: t("unavailableBody"),
    }),
    [t],
  );

  return (
    <CoverageView
      adapter={adapter}
      copy={copy}
      coverageCommand={coverageCommand}
    />
  );
}
