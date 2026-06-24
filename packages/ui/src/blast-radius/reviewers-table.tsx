import type { ReviewerEntry } from "@repowise-dev/types/blast-radius";
import { Th, Td } from "./cells";

interface ReviewersTableProps {
  rows: ReviewerEntry[];
}

export function ReviewersTable({ rows }: ReviewersTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <caption className="sr-only">Recommended reviewers</caption>
        <thead>
          <tr className="border-b border-[var(--color-border-default)]">
            <Th>Email</Th>
            <Th>Files Owned</Th>
            <Th>Avg Ownership %</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.email}
              className="group border-t border-[var(--color-table-divider)] hover:bg-[var(--color-bg-elevated)]"
            >
              <Td>
                <span className="group-hover:underline underline-offset-2">
                  {r.email}
                </span>
              </Td>
              <Td className="text-right tabular-nums">{r.files}</Td>
              <Td className="text-right tabular-nums">
                {(r.ownership_pct * 100).toFixed(1)}%
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
