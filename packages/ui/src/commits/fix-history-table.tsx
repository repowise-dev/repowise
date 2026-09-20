import { cn } from "../lib/cn";
import { formatLOC } from "../lib/format";

export interface FixHistoryRow {
  path: string;
  churn: number;
  fix_pressure: number;
}

export interface FixHistoryTableProps {
  files: FixHistoryRow[];
  className?: string;
}

/**
 * The bug-fix record of the files a commit touched.
 *
 * This leads the sheet because it is the one signal here that does not grow
 * with the diff: one line in a file fixed twenty times outranks a thousand
 * lines in files never fixed at all.
 */
export function FixHistoryTable({ files, className }: FixHistoryTableProps) {
  if (files.length === 0) return null;

  return (
    <table className={cn("w-full border-collapse text-xs", className)}>
      <caption className="sr-only">
        Files this commit touched, and how much bug-fix history each carries
      </caption>
      <thead>
        <tr>
          <th
            scope="col"
            className="border-b border-[var(--color-border-default)] pb-2 pr-3 text-left font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
          >
            File
          </th>
          <th
            scope="col"
            className="border-b border-[var(--color-border-default)] pb-2 px-3 text-right font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
          >
            Lines
          </th>
          <th
            scope="col"
            title="Past bug-fix commits on this file, weighted so a fix a year ago counts a half"
            className="cursor-help border-b border-[var(--color-border-default)] pb-2 pl-3 text-right font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
          >
            Prior fixes
          </th>
        </tr>
      </thead>
      <tbody>
        {files.map((f) => (
          <tr
            key={f.path}
            className="border-b border-[var(--color-border-default)] last:border-0"
          >
            <td className="py-2 pr-3 align-middle font-mono text-[11px] text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
              {f.path}
            </td>
            <td className="whitespace-nowrap py-2 px-3 text-right align-middle tabular-nums text-[var(--color-text-tertiary)]">
              {formatLOC(f.churn)}
            </td>
            <td className="whitespace-nowrap py-2 pl-3 text-right align-middle tabular-nums text-[var(--color-text-primary)]">
              {f.fix_pressure.toFixed(1)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
