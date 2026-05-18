"use client";

import { Wrench } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";

import { RefactoringTargetList } from "@repowise-dev/ui/health";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

import {
  getRefactoringTargets,
  type RefactoringTargetsResponse,
} from "@/lib/api/code-health";

export default function RefactoringTargetsPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;

  const { data, isLoading, error } = useSWR<RefactoringTargetsResponse>(
    `code-health-refactoring:${id}`,
    () => getRefactoringTargets(id, { limit: 50 }),
    { revalidateOnFocus: false },
  );

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1200px]">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <Wrench className="h-5 w-5 text-orange-500" />
          Refactoring targets
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Files ranked by total health impact divided by an effort
          proxy (file size bucket). High ratio = high-leverage cleanup.
        </p>
      </div>

      {isLoading ? (
        <div className="grid gap-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : error ? (
        <p className="text-sm text-red-500">
          Failed to load refactoring targets.
        </p>
      ) : !data || data.targets.length === 0 ? (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-secondary)]">
          No findings yet. Run{" "}
          <code className="px-1 rounded bg-[var(--color-bg-muted)]">
            repowise health
          </code>{" "}
          to populate this view.
        </div>
      ) : (
        <>
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Showing top {data.targets.length} of {data.total} candidates.
          </p>
          <RefactoringTargetList
            targets={data.targets}
            onSelect={(t) =>
              router.push(`/repos/${id}/files?path=${encodeURIComponent(t.file_path)}`)
            }
          />
        </>
      )}
    </div>
  );
}
