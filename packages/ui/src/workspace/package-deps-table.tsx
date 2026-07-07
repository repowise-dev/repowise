"use client";

import { Badge } from "../ui/badge";
import { EmptyState } from "../shared/empty-state";
import { VirtualizedTable } from "../shared/virtualized-table";
import type { WorkspacePackageDepEntry } from "@repowise-dev/types/workspace";

interface PackageDepsTableProps {
  deps: WorkspacePackageDepEntry[];
}

// Column-priority hide classes, mirroring the shared ResponsiveTable scale:
// priority 2 hides below md (768px), priority 3 hides below lg (1024px). The
// always-visible columns (priority 1) carry no hide class.
const HIDE_BELOW_MD = "max-md:hidden";

export function PackageDepsTable({ deps }: PackageDepsTableProps) {
  if (deps.length === 0) {
    return (
      <EmptyState
        title="No cross-repo package dependencies"
        description="No manifest in this workspace depends on a sibling repo's package."
      />
    );
  }

  const header = (
    <tr className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider">
      <th className="px-3 py-2 text-left font-medium">Source</th>
      <th className="px-3 py-2 text-left font-medium">Target</th>
      <th className={`px-3 py-2 text-left font-medium ${HIDE_BELOW_MD}`}>Manifest</th>
      <th className={`px-3 py-2 text-left font-medium ${HIDE_BELOW_MD}`}>Kind</th>
    </tr>
  );

  const renderRow = (d: WorkspacePackageDepEntry) => (
    <tr className="border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]">
      <td className="px-3 py-2 text-left">
        <Badge variant="default" className="text-xs">
          {d.source_repo}
        </Badge>
      </td>
      <td className="px-3 py-2 text-left">
        <Badge variant="default" className="text-xs">
          {d.target_repo}
        </Badge>
      </td>
      <td
        className={`px-3 py-2 text-left font-mono text-xs text-[var(--color-text-secondary)] max-w-[280px] ${HIDE_BELOW_MD}`}
      >
        <span className="block truncate" title={d.source_manifest}>
          {d.source_manifest}
        </span>
      </td>
      <td
        className={`px-3 py-2 text-left text-xs text-[var(--color-text-secondary)] ${HIDE_BELOW_MD}`}
      >
        {d.kind}
      </td>
    </tr>
  );

  return (
    <VirtualizedTable<WorkspacePackageDepEntry>
      rows={deps}
      rowKey={(d) =>
        `${d.source_repo}|${d.target_repo}|${d.source_manifest}|${d.kind}`
      }
      header={header}
      renderRow={renderRow}
      aria-label="Cross-repo package dependencies"
    />
  );
}
