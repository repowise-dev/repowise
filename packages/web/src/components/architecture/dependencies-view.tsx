"use client";

import useSWR from "swr";
import Link from "next/link";
import { Package } from "lucide-react";
import { DependencyRegistry } from "@repowise-dev/ui/dependencies";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { getExternalSystems } from "@/lib/api/external-systems";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";

export function DependenciesView({ repoId }: { repoId: string }) {
  const { data, error, isLoading, mutate } = useSWR(
    `external-systems:${repoId}`,
    () => getExternalSystems(repoId),
    { revalidateOnFocus: false },
  );

  return (
    <div className="max-w-[1600px] space-y-6 p-4 sm:p-6">
      <div>
        <h1 className="mb-1 flex items-center gap-2 text-xl font-semibold text-[var(--color-text-primary)]">
          <Package className="h-5 w-5 text-[var(--color-accent-primary)]" />
          Dependencies
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Third-party dependencies declared in this repo&apos;s manifests.
        </p>
      </div>

      {error ? (
        <ApiError
          title="Couldn't load the dependency registry"
          message={toFriendlyMessage(error)}
          onRetry={() => void mutate()}
        />
      ) : isLoading || !data ? (
        <div className="space-y-3">
          <Skeleton className="h-8 w-72" />
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-20" />
            ))}
          </div>
        </div>
      ) : (
        <DependencyRegistry
          data={data}
          renderManifestLink={(declaredIn, children) => (
            <Link
              href={fileEntityPath(`/repos/${repoId}`, declaredIn)}
              className="hover:text-[var(--color-accent-primary)] hover:underline"
            >
              {children}
            </Link>
          )}
        />
      )}
    </div>
  );
}
