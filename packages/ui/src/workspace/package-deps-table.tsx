import { Badge } from "../ui/badge";
import type { WorkspacePackageDepEntry } from "@repowise-dev/types/workspace";

interface PackageDepsTableProps {
  deps: WorkspacePackageDepEntry[];
}

export function PackageDepsTable({ deps }: PackageDepsTableProps) {
  if (deps.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)] py-4 text-center">
        No cross-repo package dependencies detected.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border-default)] text-left text-xs text-[var(--color-text-tertiary)]">
            <th className="pb-2 pr-4 font-medium">Source</th>
            <th className="pb-2 pr-4 font-medium">Target</th>
            <th className="pb-2 pr-4 font-medium">Manifest</th>
            <th className="pb-2 font-medium">Kind</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border-default)]">
          {deps.map((d, i) => (
            <tr
              key={`${d.source_repo}|${d.target_repo}|${d.source_manifest}|${i}`}
              className="hover:bg-[var(--color-bg-elevated)] transition-colors"
            >
              <td className="py-2 pr-4">
                <Badge variant="default" className="text-[11px]">{d.source_repo}</Badge>
              </td>
              <td className="py-2 pr-4">
                <Badge variant="default" className="text-[11px]">{d.target_repo}</Badge>
              </td>
              <td
                className="py-2 pr-4 font-mono text-xs text-[var(--color-text-secondary)] truncate max-w-[280px]"
                title={d.source_manifest}
              >
                {d.source_manifest}
              </td>
              <td className="py-2 text-xs text-[var(--color-text-secondary)]">{d.kind}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
