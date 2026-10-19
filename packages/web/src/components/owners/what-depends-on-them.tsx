"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { simulateOffboarding } from "@repowise-dev/ui/owners/offboarding";
import { OffboardingResultView } from "@repowise-dev/ui/owners/offboarding-result";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { useKnowledgeMatrix } from "./use-knowledge-matrix";
import { useOwnershipLabels } from "./ownership-labels";
import { contributorsHref } from "./use-contributors-url";

/**
 * "What depends on them" on a person page: the single-person off-boarding.
 *
 * Computed in the browser from the knowledge matrix rather than on the server.
 * For the person behind the truck factor the result covers thousands of files,
 * megabytes once serialised into the page, while the matrix is one gzipped
 * fetch under 100 KB that the Ownership tabs share under the same key.
 */
export function WhatDependsOnThem({ repoId, ownerKey }: { repoId: string; ownerKey: string }) {
  const t = useTranslations("views.contributors");
  const tCommon = useTranslations("common");
  const base = `/repos/${repoId}`;
  const matrix = useKnowledgeMatrix(repoId);
  const labels = useOwnershipLabels();
  const result = useMemo(
    () => (matrix.data ? simulateOffboarding(matrix.data, [ownerKey]) : null),
    [matrix.data, ownerKey],
  );

  if (matrix.error) {
    return (
      <ApiError
        title={t("loadFailedTitle")}
        message={t("matrixLoadFailed")}
        retryLabel={tCommon("retry")}
        onRetry={() => void matrix.mutate()}
      />
    );
  }
  if (!result) return <Skeleton className="h-48 w-full rounded-lg" />;

  return (
    <OffboardingResultView
      result={result}
      base={base}
      LinkComponent={Link}
      initialAreas={5}
      labels={labels}
      secondaryAction={
        <Link
          href={contributorsHref(base, "offboarding", [ownerKey])}
          className="text-xs text-[var(--color-accent-primary)] hover:underline"
        >
          {t("openInOffboarding")}
        </Link>
      }
    />
  );
}
