"use client";

import { useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import type { AffectedSignal, BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { cn } from "../lib/cn";
import { attention, type FileMarks, type Reach, type ReachedFile } from "./blast-map-model";
import { CHIP, PathRef, SignalChips } from "./blast-radius-verdict";

type Result = BlastRadiusResponse & { affected_signals?: AffectedSignal[] };
type Href = ((path: string) => string) | undefined;

const PER_MODULE = 5;
const FOLDERS_FROM = 8;

function folderOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "(root)" : path.slice(0, i);
}

function FileRow({ m, href, extra, nameOnly }: { m: FileMarks; href: string | undefined; extra?: React.ReactNode; nameOnly?: boolean | undefined }) {
  return (
    <li className="flex min-w-0 items-center justify-between gap-3 text-xs">
      <PathRef path={m.path} href={href} nameOnly={nameOnly} />
      <SignalChips m={m} extra={extra} />
    </li>
  );
}

/**
 * The changed files, the ones asking for attention first, each with what
 * makes it risky as plain chips; grouped by folder once there are enough to
 * need it. Pagerank only decides the "widely imported" chip; it is never printed.
 */
export function ChangedFilesList({ result, reach, fileHref }: { result: Result; reach: Reach; fileHref: Href }) {
  const direct = useMemo(() => new Map(result.direct_risks.map((d) => [d.path, d])), [result]);
  // The most depended-on third of this change, by pagerank. Pagerank is tiny
  // and repo-relative, so it is compared here and never printed.
  const centralCut = useMemo(() => {
    const ranks = result.direct_risks.map((d) => d.centrality).sort((a, b) => b - a);
    return ranks.length >= 3 ? (ranks[Math.floor(ranks.length / 3) - 1] ?? Infinity) : Infinity;
  }, [result]);
  // Only a file something imports can be widely imported; pagerank alone
  // ranks a file nothing imports above zero.
  const imported = useMemo(() => new Set(reach.files.filter((f) => f.depth === 1).map((f) => f.via)), [reach]);
  const grouped = reach.changed.length >= FOLDERS_FROM;
  const row = (m: FileMarks) => {
    const d = direct.get(m.path);
    const central = d && imported.has(m.path) && d.centrality > 0 && d.centrality >= centralCut;
    return (
      <FileRow
        key={m.path}
        m={m}
        href={fileHref?.(m.path)}
        nameOnly={grouped}
        extra={
          central ? (
            <span className={CHIP} title={`Among the most imported files in this change (pagerank ${d.centrality.toExponential(2)})`}>
              widely imported
            </span>
          ) : null
        }
      />
    );
  };
  if (!grouped) return <ul className="space-y-1.5">{reach.changed.map(row)}</ul>;
  const folders = new Map<string, FileMarks[]>();
  for (const m of reach.changed) folders.set(folderOf(m.path), [...(folders.get(folderOf(m.path)) ?? []), m]);
  const ordered = [...folders.entries()].sort(
    (a, b) => Math.max(...b[1].map(attention)) - Math.max(...a[1].map(attention)) || a[0].localeCompare(b[0]),
  );
  return (
    <div className="space-y-3">
      {ordered.map(([folder, files]) => (
        <div key={folder} className="space-y-1.5">
          <p className="font-mono text-xs text-[var(--color-text-tertiary)]">
            {folder}/ <span className="tabular-nums">{files.length}</span>
          </p>
          <ul className="space-y-1.5 pl-3">{files.map(row)}</ul>
        </div>
      ))}
    </div>
  );
}

interface Group {
  name: string;
  depth: number;
  files: ReachedFile[];
  risk: number;
}

function groupByModule(files: ReachedFile[]): Group[] {
  const byName = new Map<string, Group>();
  for (const f of files) {
    const g = byName.get(f.module) ?? { name: f.module, depth: f.depth, files: [], risk: 0 };
    g.files.push(f);
    g.depth = Math.min(g.depth, f.depth);
    g.risk += attention(f);
    byName.set(f.module, g);
  }
  for (const g of byName.values())
    g.files.sort((a, b) => a.depth - b.depth || attention(b) - attention(a) || a.path.localeCompare(b.path));
  return [...byName.values()].sort((a, b) => a.depth - b.depth || b.risk - a.risk || b.files.length - a.files.length);
}

function steps(depth: number): string {
  return depth === 1 ? "direct" : `${depth} steps`;
}

function ModuleRow({ g, expanded, onToggle, fileHref }: { g: Group; expanded: boolean; onToggle: () => void; fileHref: Href }) {
  const [all, setAll] = useState(false);
  const often = g.files.filter((f) => f.isHotspot).length;
  const untested = g.files.filter((f) => f.untested).length;
  const shown = all ? g.files : g.files.slice(0, PER_MODULE);
  return (
    <li>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="flex w-full min-w-0 items-center gap-2 py-1.5 text-left text-xs hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        <ChevronRight
          className={cn("h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] transition-transform", expanded && "rotate-90")}
          aria-hidden
        />
        <span className="min-w-0 flex-1 truncate font-mono text-[var(--color-text-primary)]">{g.name}</span>
        <span className="shrink-0 tabular-nums text-[var(--color-text-secondary)]">
          {g.files.length.toLocaleString()} {g.files.length === 1 ? "file" : "files"}
        </span>
        <span className="hidden w-16 shrink-0 text-right text-[var(--color-text-tertiary)] sm:inline">{steps(g.depth)}</span>
        <span className="hidden shrink-0 justify-end gap-1 sm:flex sm:w-44">
          {often > 0 && <span className={CHIP}>{often} changed often</span>}
          {untested > 0 && <span className={`${CHIP} border-dashed`}>{untested} no test</span>}
        </span>
      </button>
      {expanded && (
        <div className="space-y-1 pb-2 pl-6">
          <ul className="space-y-1.5">
            {shown.map((f) => (
              <FileRow
                key={f.path}
                m={f}
                href={fileHref?.(f.path)}
                extra={<span className="text-[10px] text-[var(--color-text-tertiary)]">{steps(f.depth)}</span>}
              />
            ))}
          </ul>
          {g.files.length > PER_MODULE && (
            <button
              type="button"
              onClick={() => setAll((v) => !v)}
              className="text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
            >
              {all ? "Show fewer" : `+${(g.files.length - PER_MODULE).toLocaleString()} more`}
            </button>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * Every source file that depends on the change, by module: each module a
 * row with its count, how far it sits and its marks, opening to its nearest
 * files first. A filter narrows modules and files by path; picking a module
 * on the map fills it in.
 */
export function AffectedByModule({
  reach,
  fileHref,
  query,
  onQueryChange,
}: {
  reach: Reach;
  fileHref: Href;
  query: string;
  onQueryChange: (q: string) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const groups = useMemo(() => groupByModule(reach.files), [reach]);
  const q = query.trim().toLowerCase();
  const shown = q
    ? groups
        .map((g) => ({ ...g, files: g.name.toLowerCase().includes(q) ? g.files : g.files.filter((f) => f.path.toLowerCase().includes(q)) }))
        .filter((g) => g.files.length > 0)
    : groups;
  const toggle = (name: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  return (
    <div className="space-y-2">
      <input
        type="search"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder="Filter by path or module"
        aria-label="Filter files that depend on the change"
        className="w-full max-w-xs rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1 text-xs text-[var(--color-text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-focus-ring)]"
      />
      <ul className="divide-y divide-[var(--color-border-default)]">
        {shown.map((g) => (
          <ModuleRow
            key={g.name}
            g={g}
            expanded={open.has(g.name) || (q !== "" && shown.length <= 3)}
            onToggle={() => toggle(g.name)}
            fileHref={fileHref}
          />
        ))}
      </ul>
      {shown.length === 0 && <p className="text-xs text-[var(--color-text-tertiary)]">No file matches.</p>}
    </div>
  );
}
