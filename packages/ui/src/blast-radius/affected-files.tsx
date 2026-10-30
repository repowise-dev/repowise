"use client";

import { useMemo, useState } from "react";
import { ChevronRight, X } from "lucide-react";
import type { AffectedSignal, BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { cn } from "../lib/cn";
import { attention, type FileMarks, type Reach, type ReachedFile } from "./blast-map-model";
import { CHIP, PathRef, SignalChips } from "./blast-radius-verdict";
import { formatNumber } from "../lib/format";

type Result = BlastRadiusResponse & { affected_signals?: AffectedSignal[] };
type Href = ((path: string) => string) | undefined;

const PER_MODULE = 5;
const FOLDERS_FROM = 8;

function folderOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "(root)" : path.slice(0, i);
}

function FileRow({
  m,
  href,
  extra,
  nameOnly,
  onHover,
}: {
  m: FileMarks;
  href: string | undefined;
  extra?: React.ReactNode;
  nameOnly?: boolean | undefined;
  onHover?: ((path: string | null) => void) | undefined;
}) {
  return (
    <li
      onMouseEnter={onHover && (() => onHover(m.path))}
      onMouseLeave={onHover && (() => onHover(null))}
      className="flex min-w-0 items-center justify-between gap-x-3 gap-y-0.5 text-xs max-sm:flex-wrap">
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

function ModuleRow({
  g,
  expanded,
  onToggle,
  fileHref,
  onHover,
}: {
  g: Group;
  expanded: boolean;
  onToggle: () => void;
  fileHref: Href;
  onHover?: ((path: string | null) => void) | undefined;
}) {
  const [all, setAll] = useState(false);
  const often = g.files.filter((f) => f.isHotspot).length;
  const shown = all ? g.files : g.files.slice(0, PER_MODULE);
  return (
    <li>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="flex w-full min-w-0 items-center gap-2 py-1.5 max-sm:min-h-10 text-left text-xs hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        <ChevronRight
          className={cn("h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] transition-transform", expanded && "rotate-90")}
          aria-hidden
        />
        <span className="min-w-0 flex-1 truncate font-mono text-[var(--color-text-primary)]">{g.name}</span>
        {/* One summary sentence, not unlabeled columns. */}
        <span className="shrink-0 truncate text-right tabular-nums text-[var(--color-text-tertiary)]">
          <span className="text-[var(--color-text-secondary)]">
            {formatNumber(g.files.length)} {g.files.length === 1 ? "file" : "files"}
          </span>
          <span className="hidden sm:inline">
            , {g.depth === 1 ? "direct" : `nearest ${g.depth} steps away`}
            {often > 0 && `, ${formatNumber(often)} changed often`}
          </span>
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
                extra={<span className={CHIP}>{steps(f.depth)}</span>}
                onHover={onHover}
              />
            ))}
          </ul>
          {g.files.length > PER_MODULE && (
            <button
              type="button"
              onClick={() => setAll((v) => !v)}
              className="text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] max-sm:min-h-10"
            >
              {all ? "Show fewer" : `+${formatNumber(g.files.length - PER_MODULE)} more`}
            </button>
          )}
        </div>
      )}
    </li>
  );
}

/** A picked block on the map: the list narrows to its files and says so. */
export interface DependentsFocus {
  label: string;
  paths: Set<string>;
}

/**
 * Every source file that depends on the change, by module: each module a
 * row with its count, how far it sits and its marks, opening to its nearest
 * files first. A filter narrows modules and files by path; picking a block
 * on the map narrows the list to that folder and step.
 */
export function AffectedByModule({
  reach,
  fileHref,
  query,
  onQueryChange,
  focus,
  onClearFocus,
  onHoverPath,
}: {
  reach: Reach;
  fileHref: Href;
  query: string;
  onQueryChange: (q: string) => void;
  focus?: DependentsFocus | undefined;
  onClearFocus?: (() => void) | undefined;
  /** The file row under the pointer, so the drawing can light its block. */
  onHoverPath?: ((path: string | null) => void) | undefined;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const groups = useMemo(
    () => groupByModule(focus ? reach.files.filter((f) => focus.paths.has(f.path)) : reach.files),
    [reach, focus],
  );
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
      {focus && (
        // The active filter as a token with a hairline border, removed with its own button.
        <p className="flex flex-wrap items-center gap-2 text-xs">
          <span className="inline-flex min-h-7 max-w-full items-center gap-1.5 rounded-full border border-[var(--color-border-hover)] py-0.5 pl-2.5 pr-1 text-[var(--color-text-secondary)]">
            <span className="min-w-0 truncate">
              Showing {focus.label}: <span className="tabular-nums">{formatNumber(focus.paths.size)}</span>{" "}
              {focus.paths.size === 1 ? "file" : "files"}
            </span>
            {onClearFocus && (
              <button
                type="button"
                onClick={onClearFocus}
                aria-label={`Clear the filter and show all ${formatNumber(reach.files.length)} files`}
                className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] pointer-coarse:h-10 pointer-coarse:w-10"
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            )}
          </span>
        </p>
      )}
      <input
        type="search"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder="Filter by path or module"
        aria-label="Filter files that depend on the change"
        className="w-full max-w-xs rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1 max-sm:min-h-10 max-sm:max-w-none text-xs text-[var(--color-text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-focus-ring)]"
      />
      <ul className="divide-y divide-[var(--color-border-default)]">
        {shown.map((g) => (
          <ModuleRow
            key={g.name}
            g={g}
            expanded={open.has(g.name) || ((q !== "" || focus !== undefined) && shown.length <= 3)}
            onToggle={() => toggle(g.name)}
            fileHref={fileHref}
            onHover={onHoverPath}
          />
        ))}
      </ul>
      {shown.length === 0 && <p className="text-xs text-[var(--color-text-tertiary)]">No file matches.</p>}
    </div>
  );
}
