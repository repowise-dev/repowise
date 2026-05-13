"use client";

import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import Link from "next/link";
import { ArrowLeft, Users } from "lucide-react";
import { OwnerProfileView } from "@repowise-dev/ui/owners/owner-profile";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { getOwnerProfile } from "@/lib/api/owners";
import type { OwnerProfileResponse } from "@/lib/api/types";

export default function OwnerProfilePage() {
  const { id, owner } = useParams<{ id: string; owner: string }>();
  const router = useRouter();
  const ownerKey = decodeURIComponent(owner);

  const { data, isLoading, error } = useSWR<OwnerProfileResponse>(
    `owner:${id}:${ownerKey}`,
    () => getOwnerProfile(id, ownerKey),
    { revalidateOnFocus: false },
  );

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1600px]">
      <div className="flex items-center justify-between">
        <Link
          href={`/repos/${id}/owners`}
          className="inline-flex items-center gap-1 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
        >
          <ArrowLeft className="h-3 w-3" /> All contributors
        </Link>
      </div>

      {isLoading && (
        <div className="space-y-4">
          <Skeleton className="h-32 w-full" />
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24" />
            ))}
          </div>
          <Skeleton className="h-96 w-full" />
        </div>
      )}

      {error && (
        <EmptyState
          icon={<Users className="h-6 w-6" />}
          title="Contributor not found"
          description="The requested profile doesn't exist in this repository's git history yet."
        />
      )}

      {data && (
        <OwnerProfileView
          owner={data}
          onSelectFile={(path) =>
            router.push(`/repos/${id}/wiki/${encodeURIComponent(path)}`)
          }
          onSelectModule={(mod) => router.push(`/repos/${id}/modules/${encodeURIComponent(mod)}`)}
          onSelectCoAuthor={(c) => {
            const k = c.email ?? `name:${c.name}`;
            router.push(`/repos/${id}/owners/${encodeURIComponent(k)}`);
          }}
        />
      )}
    </div>
  );
}
