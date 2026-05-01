import type { CochangeWarning } from "@repowise-dev/types/blast-radius";
import { Th, Td } from "./cells";

interface CochangeTableProps {
  rows: CochangeWarning[];
}

export function CochangeTable({ rows }: CochangeTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <caption className="sr-only">Co-change warnings</caption>
        <thead>
          <tr>
            <Th>Changed File</Th>
            <Th>Missing Partner</Th>
            <Th>Co-change Count</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={`${r.changed}|${r.missing_partner}|${i}`}
              className="border-t border-[var(--color-border-default)]"
            >
              <Td>
                <span className="font-mono break-all" title={r.changed}>
                  {r.changed}
                </span>
              </Td>
              <Td>
                <span className="font-mono break-all" title={r.missing_partner}>
                  {r.missing_partner}
                </span>
              </Td>
              <Td className="text-right tabular-nums">{r.score}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
