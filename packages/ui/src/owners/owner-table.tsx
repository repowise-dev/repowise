import * as React from "react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";
import { formatRelativeTimeOrNull } from "../lib/format";
import { OwnerAvatar } from "./owner-avatar";

export type OwnerSortKey =
  | "files_owned"
  | "hotspots_owned"
  | "commit_count_90d"
  | "dead_code_lines_owned"
  | "bus_factor_risk_files";

interface Column {
  key: OwnerSortKey | null;
  label: string;
  /** One line explaining what the column counts, on hover. */
  hint?: string;
  numeric: boolean;
}

// Sole-owned files are `bus_factor_risk_files` under a name a person would
// use. "Bus factor" is jargon that names the metaphor rather than the fact.
const COLUMNS: Column[] = [
  { key: "files_owned", label: "Files owned", hint: "Files where this person wrote most of the surviving lines", numeric: true },
  { key: "hotspots_owned", label: "Hotspots", hint: "Owned files that are also high-churn", numeric: true },
  { key: "bus_factor_risk_files", label: "Sole owner", hint: "Owned files nobody else has touched", numeric: true },
  { key: "dead_code_lines_owned", label: "Dead lines", hint: "Unreachable lines still attributed here", numeric: true },
  { key: "commit_count_90d", label: "Commits 90d", numeric: true },
  { key: null, label: "Last touched", numeric: false },
];

export interface OwnerTableProps {
  owners: OwnerListEntry[];
  sort: OwnerSortKey;
  onSortChange?: ((key: OwnerSortKey) => void) | undefined;
  /** Link target per row. Preferred over `onSelect`: it keeps the table a
   *  server component and gives every row a real URL to middle-click. */
  hrefFor?: ((owner: OwnerListEntry) => string) | undefined;
  onSelect?: ((owner: OwnerListEntry) => void) | undefined;
  LinkComponent?: React.ElementType | undefined;
}

/**
 * The contributor directory as hairline rows.
 *
 * This replaced a 3-up grid of bordered cards, for three reasons. Cards forced
 * `truncate` on both the name and the email, so a long name was reported to
 * the reader as a shorter name — a layout decision presented as missing data.
 * A grid also makes the one thing people come here to do impossible: compare
 * two contributors on the same measure, which needs the figures in a column.
 * And N contributors meant N bordered boxes at identical weight, so nothing
 * led.
 *
 * Colour stays off the counts. A number being large is not the same as a
 * number being bad, and green/amber/red belong to health bands. Sole
 * ownership gets a quiet marker instead, and only when it is non-zero: a
 * badge every row carries says nothing, so a clean column means nothing to
 * chase.
 */
export function OwnerTable({
  owners,
  sort,
  onSortChange,
  hrefFor,
  onSelect,
  LinkComponent,
}: OwnerTableProps) {
  const A = LinkComponent ?? "a";

  return (
    <div className="-mx-[var(--page-pad)] overflow-x-auto px-[var(--page-pad)] sm:mx-0 sm:px-0">
      <table className="w-full border-collapse text-[13.5px]">
        <thead>
          <tr>
            <th
              scope="col"
              className="border-b border-[var(--color-border-default)] pb-2 pr-3 text-left font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
            >
              Contributor
            </th>
            {COLUMNS.map((c) => {
              const active = c.key != null && c.key === sort;
              const head = (
                <span title={c.hint} className={c.hint ? "cursor-help" : undefined}>
                  {c.label}
                </span>
              );
              return (
                <th
                  key={c.label}
                  scope="col"
                  aria-sort={active ? "descending" : undefined}
                  className={`whitespace-nowrap border-b border-[var(--color-border-default)] px-3 pb-2 font-mono text-[10px] font-normal uppercase tracking-[0.12em] ${
                    c.numeric ? "text-right" : "text-left"
                  } ${active ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-tertiary)]"}`}
                >
                  {c.key && onSortChange ? (
                    <button
                      type="button"
                      onClick={() => onSortChange(c.key as OwnerSortKey)}
                      className="tracking-[0.12em] hover:text-[var(--color-text-primary)]"
                    >
                      {head}
                      {active && <span aria-hidden> ↓</span>}
                    </button>
                  ) : (
                    head
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {owners.map((o) => {
            const displayName = o.name || o.email || "unknown";
            const href = hrefFor?.(o);
            return (
              <tr
                key={o.key}
                onClick={onSelect ? () => onSelect(o) : undefined}
                className={`border-b border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)] ${
                  onSelect ? "cursor-pointer" : ""
                }`}
              >
                <td className="py-2.5 pr-3 align-top">
                  <span className="flex items-start gap-2.5">
                    <OwnerAvatar name={o.name} email={o.email} size="sm" className="mt-0.5" />
                    <span className="min-w-0">
                      {/* No truncation: if a name needs an ellipsis the
                          layout is wrong, and this column has the room.
                          The anchor sits on the name rather than wrapping the
                          row — a row-sized link means nesting a table inside a
                          cell, which unpicks the column alignment the table
                          exists for. */}
                      {href ? (
                        <A
                          href={href}
                          className="block font-medium text-[var(--color-text-primary)] no-underline hover:text-[var(--color-accent-primary)] [text-wrap:pretty]"
                        >
                          {displayName}
                        </A>
                      ) : (
                        <span className="block font-medium text-[var(--color-text-primary)] [text-wrap:pretty]">
                          {displayName}
                        </span>
                      )}
                      {o.email && o.email !== o.name && (
                        <span className="mt-0.5 block font-mono text-[11.5px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
                          {o.email}
                        </span>
                      )}
                    </span>
                  </span>
                </td>
                <Num value={o.files_owned} emphasis />
                <Num value={o.hotspots_owned} />
                <Num
                  value={o.bus_factor_risk_files}
                  // Sole ownership alone marks nearly every row on a real
                  // repo, and a badge every row carries says nothing. The
                  // signal is sole ownership held by someone who has stopped
                  // committing: that is knowledge with no second reader and
                  // nobody left to ask.
                  marker={o.bus_factor_risk_files > 0 && o.commit_count_90d === 0}
                  markerTitle="Sole-owned files held by someone inactive for 90 days"
                />
                <Num value={o.dead_code_lines_owned} />
                <Num value={o.commit_count_90d} />
                <td className="whitespace-nowrap py-2.5 pl-3 align-top text-[var(--color-text-tertiary)]">
                  {formatRelativeTimeOrNull(o.last_commit_at) ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Num({
  value,
  emphasis,
  marker,
  markerTitle,
}: {
  value: number;
  emphasis?: boolean;
  marker?: boolean;
  markerTitle?: string;
}) {
  return (
    <td
      className={`whitespace-nowrap px-3 py-2.5 text-right align-top tabular-nums ${
        emphasis ? "font-semibold text-[var(--color-text-primary)]" : ""
      }`}
    >
      {value > 0 ? value.toLocaleString() : <span className="text-[var(--color-text-tertiary)]">—</span>}
      {marker && (
        <span
          title={markerTitle}
          className="ml-1.5 inline-block h-[5px] w-[5px] shrink-0 rounded-full bg-[var(--color-warning)] align-middle"
        />
      )}
    </td>
  );
}
