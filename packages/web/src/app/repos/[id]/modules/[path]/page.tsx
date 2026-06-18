"use client";

import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import Link from "next/link";
import { Folder } from "lucide-react";
import {
  ModuleHealthDetailView,
  ModuleDetailShell,
} from "@repowise-dev/ui/modules";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { getModuleHealth } from "@/lib/api/modules";
import type { ModuleHealthDetail } from "@/lib/api/types";

export default function ModuleHealthPage() {
  const { id, path } = useParams<{ id: string; path: string }>();
  const router = useRouter();
  const modulePath = decodeURIComponent(path);

  const { data, isLoading, error } = useSWR<ModuleHealthDetail>(
    `module-health:${id}:${modulePath}`,
    () => getModuleHealth(id, modulePath),
    { revalidateOnFocus: false },
  );

  return (
    <ModuleDetailShell
      backHref={`/repos/${id}/code-health?tab=modules`}
      LinkComponent={Link}
    >
      {isLoading && (
        <div className="space-y-4">
          <Skeleton className="h-40 w-full" />
          <div className="grid gap-3 lg:grid-cols-3">
            <Skeleton className="lg:col-span-2 h-64" />
            <Skeleton className="h-64" />
          </div>
        </div>
      )}

      {error && (
        <EmptyState
          icon={<Folder className="h-6 w-6" />}
          title="Module not found"
          description="The requested module path doesn't exist in this repository's index."
        />
      )}

      {data && (
        <ModuleHealthDetailView
          module={data}
          breadcrumb={[
            { label: "Modules", href: `/repos/${id}/code-health?tab=modules` },
            { label: modulePath.split("/").pop() || modulePath },
          ]}
          LinkComponent={Link}
          onSelectOwner={(o) => {
            const key = o.email ?? `name:${o.name}`;
            router.push(`/repos/${id}/owners/${encodeURIComponent(key)}`);
          }}
          onSelectFile={(p) => router.push(fileEntityPath(`/repos/${id}`, p))}
          onSelectDecision={(decisionId) =>
            router.push(`/repos/${id}/decisions/${encodeURIComponent(decisionId)}`)
          }
        />
      )}
    </ModuleDetailShell>
  );
}
