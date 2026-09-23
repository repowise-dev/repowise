"use client";

import { BreakingChangesView } from "@repowise-dev/ui/workspace/breaking-changes-view";
import { OverviewSection } from "@repowise-dev/ui/overview/section";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import { useWorkspaceBreakingChanges } from "@/lib/hooks/use-workspace";
import { useOpenContract } from "./contract-drawer-host";

/**
 * The breaking-change report on the contracts page.
 *
 * A client boundary because the report is fetched per view rather than at build
 * time: it is written by the most recent workspace update, and a page cached for
 * 30 seconds would report a stale all-clear. It owns its section heading so the
 * explanation of what the list holds appears only when there is a list; with no
 * findings the section is its heading and one sentence.
 */
export function BreakingChangesSection({
  repoIds,
}: {
  /** Repo alias to indexed repo id; a never-indexed repo's references stay text. */
  repoIds: Record<string, string>;
}) {
  const open = useOpenContract();
  const { data, isLoading } = useWorkspaceBreakingChanges();
  const hasFindings = (data?.changes.length ?? 0) > 0;

  const prefixFor = (repo: string): string | null => {
    const id = repoIds[repo];
    return id ? `/repos/${id}` : null;
  };

  return (
    <OverviewSection
      title="Breaking changes"
      {...(hasFindings
        ? {
            description:
              "Provider contracts that changed in the most recent workspace update, and the consumers linked to them. Breaking first, then warnings.",
          }
        : {})}
    >
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
          open({
            repo: change.provider_repo,
            file_path: change.provider_file,
            contract_id: change.contract_id,
          })
        }
      />
    </OverviewSection>
  );
}
