import { Badge } from "../ui/badge";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import type { WorkspacePackageDepEntry } from "@repowise-dev/types/workspace";

interface PackageDepsTableProps {
  deps: WorkspacePackageDepEntry[];
}

const COLUMNS: ResponsiveColumn<WorkspacePackageDepEntry>[] = [
  {
    key: "source",
    header: "Source",
    priority: 1,
    render: (d) => (
      <Badge variant="default" className="text-xs">
        {d.source_repo}
      </Badge>
    ),
  },
  {
    key: "target",
    header: "Target",
    priority: 1,
    render: (d) => (
      <Badge variant="default" className="text-xs">
        {d.target_repo}
      </Badge>
    ),
  },
  {
    key: "manifest",
    header: "Manifest",
    priority: 2,
    cellClassName: "font-mono text-xs text-[var(--color-text-secondary)] max-w-[280px]",
    render: (d) => (
      <span className="block truncate" title={d.source_manifest}>
        {d.source_manifest}
      </span>
    ),
  },
  {
    key: "kind",
    header: "Kind",
    priority: 2,
    cellClassName: "text-xs text-[var(--color-text-secondary)]",
    render: (d) => d.kind,
  },
];

export function PackageDepsTable({ deps }: PackageDepsTableProps) {
  return (
    <ResponsiveTable
      columns={COLUMNS}
      rows={deps}
      rowKey={(d) =>
        `${d.source_repo}|${d.target_repo}|${d.source_manifest}|${d.kind}`
      }
      bare
      empty={
        <EmptyState
          title="No cross-repo package dependencies"
          description="No manifest in this workspace depends on a sibling repo's package."
        />
      }
    />
  );
}
