import type { TransitiveEntry } from "@repowise-dev/types/blast-radius";
import { Th, Td } from "./cells";

interface TransitiveTableProps {
  rows: TransitiveEntry[];
}

export function TransitiveTable({ rows }: TransitiveTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <caption className="sr-only">Transitively affected files</caption>
        <thead>
          <tr className="border-b border-[var(--color-border-default)]">
            <Th>File</Th>
            <Th>Depth</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.path}
              className="group border-t border-[var(--color-table-divider)] hover:bg-[var(--color-bg-elevated)]"
            >
              <Td>
                <span
                  className="font-mono break-all group-hover:underline underline-offset-2"
                  title={r.path}
                >
                  {r.path}
                </span>
              </Td>
              <Td className="text-right tabular-nums">{r.depth}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
