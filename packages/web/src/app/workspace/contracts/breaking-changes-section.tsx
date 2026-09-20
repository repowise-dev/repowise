"use client";

import { useRouter } from "next/navigation";
import { BreakingChangesView } from "@repowise-dev/ui/workspace/breaking-changes-view";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import { useWorkspaceBreakingChanges } from "@/lib/hooks/use-workspace";
import { contractDetailHref } from "./contract-href";

/**
 * The breaking-change report on the contracts page.
 *
 * A client boundary because the report is fetched per view rather than at build
 * time: it is written by the most recent workspace update, and a page cached for
 * 30 seconds would report a stale all-clear.
 *
 * The alias-to-repo-id map comes from the page, which already loads the
 * workspace. A change names a repo by its workspace alias; the per-repo routes
 * are keyed on the indexed repo id, and a repo that was never indexed has none,
 * so those references stay plain text instead of linking into a missing page.
 */
export function BreakingChangesSection({
  repoIds,
}: {
  repoIds: Record<string, string>;
}) {
  const router = useRouter();
  const { data, isLoading } = useWorkspaceBreakingChanges();

  const prefixFor = (repo: string): string | null => {
    const id = repoIds[repo];
    return id ? `/repos/${id}` : null;
  };

  return (
    <BreakingChangesView
      report={data}
      loading={isLoading}
      links={{
        symbolHref: (repo, symbolId) => {
          const prefix = prefixFor(repo);
          return prefix ? symbolEntityPath(prefix, symbolId) : null;
        },
        fileHref: (repo, file) => {
          const prefix = prefixFor(repo);
          return prefix ? fileEntityPath(prefix, file) : null;
        },
      }}
      onSelectContract={(_contractId, change) =>
        router.push(
          contractDetailHref({
            repo: change.provider_repo,
            file_path: change.provider_file,
            contract_id: change.contract_id,
          }),
        )
      }
    />
  );
}
