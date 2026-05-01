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
          <tr>
            <Th>File</Th>
            <Th>Depth</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.path} className="border-t border-[var(--color-border-default)]">
              <Td>
                <span className="font-mono break-all" title={r.path}>
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
