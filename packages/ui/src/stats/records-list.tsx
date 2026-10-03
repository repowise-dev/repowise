import * as React from "react";
import type { StatsRecords } from "@repowise-dev/types/stats";
import { formatNumber } from "../lib/format";

export interface RecordLinks {
  fileHref?: ((path: string) => string) | undefined;
  commitHref?: ((sha: string) => string) | undefined;
  LinkComponent?: React.ElementType | undefined;
}

interface RecordRow {
  key: string;
  title: string;
  /** The subject: a path, a symbol, a commit subject or a sentence. Never truncated. */
  primary: string;
  mono?: boolean;
  detail: string;
  href?: string | undefined;
}

type Href = (value: string) => string | undefined;

/** A path that wraps after a slash instead of mid-name. */
function breakable(text: string): React.ReactNode {
  const parts = text.split("/");
  return parts.map((part, i) => (
    <React.Fragment key={i}>
      {part}
      {i < parts.length - 1 && (
        <>
          /<wbr />
        </>
      )}
    </React.Fragment>
  ));
}

function fileName(path: string): string {
  return path.split("/").pop() || path;
}

function fileRow(key: string, title: string, path: string, detail: string, file: Href): RecordRow {
  return { key, title, primary: path, mono: true, detail, href: file(path) };
}

function fileRows(r: StatsRecords, file: Href): RecordRow[] {
  const rows: RecordRow[] = [];
  if (r.largest_file) {
    const f = r.largest_file;
    rows.push(fileRow("largest", "Largest file", f.path, `${formatNumber(f.nloc)} lines of code`, file));
  }
  // Usually the file holding the most complex symbol, which that row already
  // names with the function attached.
  if (r.gnarliest_file && r.most_complex_symbol?.file_path !== r.gnarliest_file.path) {
    const f = r.gnarliest_file;
    const detail = `cyclomatic complexity ${formatNumber(f.max_ccn)} in one function`;
    rows.push(fileRow("gnarliest", "Gnarliest file", f.path, detail, file));
  }
  if (r.most_changed_file) {
    const f = r.most_changed_file;
    const detail = `changed in ${formatNumber(f.commit_count)} commits`;
    rows.push(fileRow("changed", "Most-changed file", f.path, detail, file));
  }
  if (r.most_central_file) {
    const c = r.most_central_file;
    const title = c.import_count != null ? "Most imported file" : "Most central file";
    const detail =
      c.import_count != null
        ? `imported by ${formatNumber(c.import_count)} files`
        : `PageRank ${c.pagerank.toFixed(4)}`;
    rows.push(fileRow("central", title, c.path, detail, file));
  }
  if (r.day_one_files) {
    const d = r.day_one_files;
    rows.push({
      key: "day-one",
      title: "Here since day one",
      primary: `${formatNumber(d.count)} ${d.count === 1 ? "file dates" : "files date"} back to the root commit`,
      detail: `of ${formatNumber(d.of)} files with a full history`,
    });
  }
  return rows;
}

function functionRow(
  key: string,
  title: string,
  f: { name: string; file_path: string },
  detail: string,
  file: Href,
): RecordRow {
  return {
    key,
    title,
    primary: f.name,
    mono: true,
    detail: `${detail}, in ${fileName(f.file_path)}`,
    href: file(f.file_path),
  };
}

function functionRows(r: StatsRecords, file: Href): RecordRow[] {
  const rows: RecordRow[] = [];
  if (r.longest_function) {
    const f = r.longest_function;
    rows.push(functionRow("longest-fn", "Longest function", f, `${formatNumber(f.lines)} lines`, file));
  }
  if (r.most_patched_function) {
    const f = r.most_patched_function;
    const detail = `${formatNumber(f.mod_count)} commits own its current lines`;
    rows.push(functionRow("patched", "Most patched function", f, detail, file));
  }
  if (r.most_complex_symbol) {
    const s = r.most_complex_symbol;
    const f = { name: s.name, file_path: s.file_path };
    rows.push(functionRow("complex", "Most complex symbol", f, `complexity ${formatNumber(s.complexity)}`, file));
  }
  if (r.longest_name) {
    const f = r.longest_name;
    rows.push(functionRow("longest-name", "Longest name", f, `${f.name.length} characters`, file));
  }
  if (r.most_common_name) {
    const c = r.most_common_name;
    rows.push({
      key: "common-name",
      title: "Most reused name",
      primary: c.name,
      mono: true,
      detail: `${formatNumber(c.count)} different functions share it`,
    });
  }
  if (r.symbol_shape && r.symbol_shape.total > 0) {
    const s = r.symbol_shape;
    rows.push({
      key: "documented",
      title: "Documented symbols",
      primary: `${Math.round(s.documented_pct)}% carry a docstring`,
      detail: `${formatNumber(s.documented_count)} of ${formatNumber(s.total)} symbols, and ${Math.round(
        s.async_pct,
      )}% of them are async`,
    });
  }
  return rows;
}

function commitRows(r: StatsRecords, commit: Href): RecordRow[] {
  const row = (key: string, title: string, c: { sha: string; subject: string }, detail: string) => ({
    key,
    title,
    primary: c.subject || c.sha.slice(0, 10),
    detail,
    href: commit(c.sha),
  });
  const rows: RecordRow[] = [];
  if (r.biggest_commit) {
    const c = r.biggest_commit;
    const detail = `${formatNumber(c.lines_changed)} lines across ${formatNumber(c.files_changed)} files`;
    rows.push(row("biggest", "Biggest commit", c, detail));
  }
  if (r.widest_commit) {
    const c = r.widest_commit;
    rows.push(row("widest", "Widest commit", c, `touched ${formatNumber(c.files_changed)} files at once`));
  }
  if (r.biggest_purge) {
    const c = r.biggest_purge;
    const detail = `removed ${formatNumber(c.lines_deleted - c.lines_added)} more lines than it added`;
    rows.push(row("purge", "Biggest cleanup", c, detail));
  }
  return rows;
}

function structureRows(r: StatsRecords): RecordRow[] {
  const c = r.largest_cycle;
  if (!c || c.files <= 1) return [];
  return [
    {
      key: "cycle",
      title: "Largest import cycle",
      primary: `${formatNumber(c.files)} files import each other in a loop`,
      detail: `${formatNumber(c.cycle_count)} circular cluster${c.cycle_count === 1 ? "" : "s"} in total`,
    },
  ];
}

/**
 * The repo's records: biggest, longest, most patched, most tangled.
 *
 * Hairline rows grouped by subject. The subject is never truncated, and a row
 * whose subject has a page links to it. `commitScope` qualifies the commit
 * group when it was drawn from part of the history.
 */
export function RecordsList({
  records,
  commitScope,
  ...links
}: { records: StatsRecords; commitScope?: string | undefined } & RecordLinks) {
  const file: Href = (path) => links.fileHref?.(path);
  const commit: Href = (sha) => links.commitHref?.(sha);
  const groups = [
    { title: "Files", rows: fileRows(records, file) },
    { title: "Functions", rows: functionRows(records, file) },
    { title: commitScope ? `Commits, ${commitScope}` : "Commits", rows: commitRows(records, commit) },
    { title: "Structure", rows: structureRows(records) },
  ].filter((g) => g.rows.length > 0);
  if (groups.length === 0) return null;
  const A = links.LinkComponent ?? "a";

  return (
    <div className="grid grid-cols-1 gap-x-10 gap-y-8 lg:grid-cols-2">
      {groups.map((g) => (
        <section key={g.title} aria-label={g.title} className="min-w-0">
          <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            {g.title}
          </h3>
          <dl className="mt-2 divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
            {g.rows.map((row) => {
              const text = row.mono ? breakable(row.primary) : row.primary;
              return (
                <div key={row.key} className="min-w-0 py-3">
                  <dt className="text-xs text-[var(--color-text-tertiary)]">{row.title}</dt>
                  <dd
                    className={`mt-0.5 break-words text-[15px] font-medium text-[var(--color-text-primary)] ${
                      row.mono ? "font-mono" : ""
                    }`}
                  >
                    {row.href ? (
                      <A
                        href={row.href}
                        className="text-[var(--color-text-primary)] underline decoration-[var(--color-border-default)] underline-offset-4 hover:text-[var(--color-accent-primary)] hover:decoration-current focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent-primary)]"
                      >
                        {text}
                      </A>
                    ) : (
                      text
                    )}
                  </dd>
                  <dd className="mt-0.5 text-xs tabular-nums text-[var(--color-text-secondary)]">
                    {row.detail}
                  </dd>
                </div>
              );
            })}
          </dl>
        </section>
      ))}
    </div>
  );
}
