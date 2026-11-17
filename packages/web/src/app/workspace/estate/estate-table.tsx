import type { ReactNode } from "react";

export interface EstateColumn {
  label: string;
  /** Figures align right so their digits line up. */
  numeric?: boolean;
}

/**
 * A plain, server-rendered table in the hotspot-table style: mono micro-label
 * headers, hairline rows, tabular figures, and its own horizontal scroll so
 * the page never scrolls sideways at phone width.
 */
export function EstateTable({
  caption,
  columns,
  rows,
  note,
}: {
  caption: string;
  columns: EstateColumn[];
  rows: { key: string; cells: ReactNode[] }[];
  /** Under the table, e.g. how many rows were left out. */
  note?: string | undefined;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="-mx-[var(--page-pad)] overflow-x-auto px-[var(--page-pad)] sm:mx-0 sm:px-0">
        <table className="w-full min-w-[560px] border-collapse text-xs">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr className="border-b border-[var(--color-border-default)] text-left font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              {columns.map((c) => (
                <th
                  key={c.label}
                  scope="col"
                  className={`pb-2 pr-4 font-medium last:pr-0 ${c.numeric ? "text-right" : ""}`}
                >
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.key}
                className="border-b border-[var(--color-border-default)] align-top last:border-b-0"
              >
                {row.cells.map((cell, i) => (
                  <td
                    key={columns[i]?.label ?? i}
                    className={`py-2.5 pr-4 text-[var(--color-text-secondary)] last:pr-0 ${
                      columns[i]?.numeric ? "text-right font-mono tabular-nums" : ""
                    }`}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {note ? <p className="text-xs text-[var(--color-text-tertiary)]">{note}</p> : null}
    </div>
  );
}
