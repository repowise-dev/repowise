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
          <tr>
            <Th>Email</Th>
            <Th>Files Owned</Th>
            <Th>Avg Ownership %</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.email} className="border-t border-[var(--color-border-default)]">
              <Td>{r.email}</Td>
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
