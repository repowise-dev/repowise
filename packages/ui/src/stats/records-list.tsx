import * as React from "react";
import type { StatsRecords } from "@repowise-dev/types/stats";
import { formatNumber } from "../lib/format";
import { splitPath } from "../lib/path-parts";
import { FilePath } from "../shared/file-path";
import { shortCount } from "./numbers";

export interface RecordLinks {
  fileHref?: ((path: string) => string) | undefined;
  commitHref?: ((sha: string) => string) | undefined;
  LinkComponent?: React.ElementType | undefined;
}

interface RecordRow {
  key: string;
  title: string;
  /** The holder: a path, a symbol, a commit subject or a sentence. Never truncated. */
  primary: string;
  mono?: boolean;
  /** `primary` is a repository path. */
  isPath?: boolean;
  /** A quiet line under the holder. */
  sub?: string | undefined;
  /** The figure, already formatted, and its unit. */
  figure?: string | undefined;
  unit?: string | undefined;
  /** Exact value behind a shortened figure. */
  exact?: string | undefined;
  href?: string | undefined;
}

type Href = (value: string) => string | undefined;

function count(n: number, unit: string): Pick<RecordRow, "figure" | "unit" | "exact"> {
  const figure = shortCount(n);
  return { figure, unit, exact: figure === formatNumber(n) ? undefined : formatNumber(n) };
}

function symbolRow(
  key: string,
  title: string,
  f: { name: string; file_path: string },
  file: Href,
  rest: Partial<RecordRow> = {},
): RecordRow {
  return {
    key,
    title,
    primary: f.name,
    mono: true,
    sub: `in ${splitPath(f.file_path).name}`,
    href: file(f.file_path),
    ...rest,
  };
}

function functionRows(r: StatsRecords, file: Href): RecordRow[] {
  const rows: RecordRow[] = [];
  if (r.longest_function) {
    const f = r.longest_function;
    rows.push(symbolRow("longest-fn", "Longest function", f, file, count(f.lines, "lines")));
  }
  if (r.most_complex_symbol) {
    const s = r.most_complex_symbol;
    const f = { name: s.name, file_path: s.file_path };
    rows.push(symbolRow("complex", "Most complex symbol", f, file, count(s.complexity, "complexity")));
  }
  if (r.most_patched_function) {
    const f = r.most_patched_function;
    rows.push(
      symbolRow("patched", "Most patched function", f, file, {
        ...count(f.mod_count, "commits"),
        sub: `in ${splitPath(f.file_path).name}, counting commits that own its current lines`,
      }),
    );
  }
  if (r.longest_name) {
    const f = r.longest_name;
    rows.push(symbolRow("longest-name", "Longest name", f, file, count(f.name.length, "characters")));
  }
  if (r.most_common_name) {
    const c = r.most_common_name;
    rows.push({
      key: "common-name",
      title: "Most reused name",
      primary: c.name,
      mono: true,
      sub: "different functions share it",
      ...count(c.count, "functions"),
    });
  }
  if (r.symbol_shape && r.symbol_shape.total > 0) {
    const s = r.symbol_shape;
    // Both shares are of every symbol: async is not a share of the documented ones.
    rows.push({
      key: "documented",
      title: "Documented symbols",
      primary: "Symbols that carry a docstring",
      sub: `${formatNumber(s.documented_count)} of ${formatNumber(s.total)} symbols. Separately, ${Math.round(
        s.async_pct,
      )}% of all symbols are async.`,
      figure: `${Math.round(s.documented_pct)}%`,
    });
  }
  return rows;
}

function fileRows(r: StatsRecords, file: Href): RecordRow[] {
  const rows: RecordRow[] = [];
  const path = (key: string, title: string, p: string, rest: Partial<RecordRow>): RecordRow => ({
    key,
    title,
    primary: p,
    mono: true,
    isPath: true,
    href: file(p),
    ...rest,
  });
  if (r.most_changed_file) {
    const f = r.most_changed_file;
    rows.push(path("changed", "Most-changed file", f.path, count(f.commit_count, "commits")));
  }
  if (r.most_central_file) {
    const c = r.most_central_file;
    rows.push(
      c.import_count != null
        ? path("central", "Most imported file", c.path, count(c.import_count, "importers"))
        : path("central", "Most central file", c.path, { sub: "ranked by import graph centrality" }),
    );
  }
  if (r.largest_file) {
    const f = r.largest_file;
    rows.push(path("largest", "Largest file", f.path, count(f.nloc, "lines")));
  }
  // Usually the file holding the most complex symbol, which that row already names.
  if (r.gnarliest_file && r.most_complex_symbol?.file_path !== r.gnarliest_file.path) {
    const f = r.gnarliest_file;
    rows.push(
      path("gnarliest", "Gnarliest file", f.path, {
        ...count(f.max_ccn, "complexity"),
        sub: "highest complexity in one function",
      }),
    );
  }
  if (r.day_one_files) {
    const d = r.day_one_files;
    rows.push({
      key: "day-one",
      title: "Here since day one",
      primary: "Files that date back to the root commit",
      sub: `of ${formatNumber(d.of)} with a full history`,
      ...count(d.count, d.count === 1 ? "file" : "files"),
    });
  }
  if (r.largest_cycle && r.largest_cycle.files > 1) {
    const c = r.largest_cycle;
    rows.push({
      key: "cycle",
      title: "Largest import cycle",
      primary: "Files that import each other in a loop",
      sub: `${formatNumber(c.cycle_count)} circular cluster${c.cycle_count === 1 ? "" : "s"} in total`,
      ...count(c.files, "files"),
    });
  }
  return rows;
}

function commitRows(r: StatsRecords, commit: Href): RecordRow[] {
  const row = (
    key: string,
    title: string,
    c: { sha: string; subject: string },
    rest: Partial<RecordRow>,
  ): RecordRow => ({ key, title, primary: c.subject || c.sha.slice(0, 10), href: commit(c.sha), ...rest });
  const rows: RecordRow[] = [];
  if (r.biggest_commit) {
    const c = r.biggest_commit;
    rows.push(
      row("biggest", "Biggest commit", c, {
        ...count(c.lines_changed, "lines"),
        sub: `across ${formatNumber(c.files_changed)} files`,
      }),
    );
  }
  if (r.widest_commit) {
    const c = r.widest_commit;
    rows.push(
      row("widest", "Widest commit", c, {
        ...count(c.files_changed, "files"),
        sub: `${shortCount(c.lines_changed)} lines changed`,
      }),
    );
  }
  if (r.biggest_purge) {
    const c = r.biggest_purge;
    rows.push(
      row("purge", "Biggest cleanup", c, {
        ...count(c.lines_deleted - c.lines_added, "lines net removed"),
        sub: `${formatNumber(c.lines_added)} added, ${formatNumber(c.lines_deleted)} removed`,
      }),
    );
  }
  return rows;
}

const MICRO = "font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

/** One record: name, holder (linked when it has a page) and figure. */
function RecordTableRow({ row, A }: { row: RecordRow; A: React.ElementType }) {
  const text = row.isPath ? <FilePath path={row.primary} mode="name-first" fold /> : row.primary;
  return (
    <tr className="border-b border-[var(--color-border-default)] align-baseline">
      <th
        scope="row"
        className="invisible overflow-hidden py-2.5 text-left text-xs font-normal text-[var(--color-text-secondary)] sm:visible sm:pr-3"
      >
        {row.title}
      </th>
      <td className="min-w-0 py-2.5 pr-3">
        <span className="block text-xs text-[var(--color-text-secondary)] sm:hidden">{row.title}</span>
        <span
          className={`block break-words font-medium text-[var(--color-text-primary)] [overflow-wrap:anywhere] ${
            row.mono ? "font-mono text-[15px]" : ""
          }`}
        >
          {row.href ? (
            <A
              href={row.href}
              className="text-[var(--color-text-primary)] underline decoration-[var(--color-border-default)] underline-offset-4 hover:text-[var(--color-accent-primary)] hover:decoration-current focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-focus-ring)]"
            >
              {text}
            </A>
          ) : (
            text
          )}
        </span>
        {row.sub && <span className="mt-0.5 block text-xs text-[var(--color-text-tertiary)]">{row.sub}</span>}
      </td>
      <td className="py-2.5 text-right tabular-nums" title={row.exact}>
        {row.figure && (
          <>
            {/* A shortened figure reads aloud as its exact value. */}
            <span className="font-medium text-[var(--color-text-primary)]" aria-hidden={row.exact ? true : undefined}>
              {row.figure}
            </span>
            {row.exact && <span className="sr-only">{row.exact}</span>}
            {row.unit && <span className="block text-xs text-[var(--color-text-tertiary)]">{row.unit}</span>}
          </>
        )}
      </td>
    </tr>
  );
}

/**
 * The repo's records as one table: record, holder, figure.
 *
 * The figure sits on the right where a reader scans for it, rather than as a
 * quiet line under the holder. On phones the record name moves above the
 * holder so the table keeps two columns and never scrolls sideways. The holder
 * is never truncated, and a holder with a page links to it. `commitScope`
 * qualifies the commit group when it was drawn from part of the history.
 */
export function RecordsList({
  records,
  commitScope,
  ...links
}: { records: StatsRecords; commitScope?: string | undefined } & RecordLinks) {
  const file: Href = (path) => links.fileHref?.(path);
  const commit: Href = (sha) => links.commitHref?.(sha);
  const groups = [
    { title: "Functions", rows: functionRows(records, file) },
    { title: "Files", rows: fileRows(records, file) },
    { title: commitScope ? `Commits, ${commitScope}` : "Commits", rows: commitRows(records, commit) },
  ].filter((g) => g.rows.length > 0);
  if (groups.length === 0) return null;
  const A = links.LinkComponent ?? "a";
  const total = groups.reduce((a, g) => a + g.rows.length, 0);

  return (
    <section aria-label="Records" className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">Records</h2>
        <span className={MICRO}>{total} records, production code only</span>
      </div>
      {/* Fixed layout so an unbreakable symbol name wraps inside the holder
          column instead of pushing the figures out of the grid cell. */}
      <table className="w-full table-fixed border-collapse text-[15px]">
        <colgroup>
          {/* Zero-width on phones: the record name moves above the holder. */}
          <col className="w-0 sm:w-[9.5rem]" />
          <col />
          <col className="w-[6.5rem] sm:w-[8.5rem]" />
        </colgroup>
        <thead>
          <tr className="border-b border-[var(--color-border-default)]">
            <th scope="col" className={`${MICRO} invisible overflow-hidden pb-2 text-left sm:visible sm:pr-3`}>
              Record
            </th>
            <th scope="col" className={`${MICRO} pb-2 pr-3 text-left`}>
              Holder
            </th>
            <th scope="col" className={`${MICRO} pb-2 text-right`}>
              Figure
            </th>
          </tr>
        </thead>
        {groups.map((g) => (
          <tbody key={g.title}>
            <tr className="border-b border-[var(--color-border-default)]">
              <th scope="colgroup" colSpan={3} className={`${MICRO} pb-2 pt-5 text-left`}>
                {g.title}
              </th>
            </tr>
            {g.rows.map((row) => (
              <RecordTableRow key={row.key} row={row} A={A} />
            ))}
          </tbody>
        ))}
      </table>
    </section>
  );
}
