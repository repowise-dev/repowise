"use client";

/**
 * CodeGraphTree — the dependency graph as a collapsible outline.
 *
 * The canvas draws one circle per file and one line per import, and at repo
 * scale neither the line nor its label can be read: `imported_names` reaches
 * the client on every edge already, and nothing renders it. This is the same
 * payload in the other shape a reader knows how to walk. A directory tree, with
 * every file openable in place to show the names it imports and which file each
 * name comes from.
 *
 * Nothing here is fetched, curated or hardcoded: the outline, the counts and
 * the type lists are all derived from the payload the canvas is drawing, so the
 * two surfaces cannot disagree, and a revalidation or a stepped-up node cap
 * updates this one too.
 *
 * Presentational. The component owns only which rows are open; data and links
 * arrive as props, and `packages/web` hosts the wrapper that fetches them.
 */

import { useMemo, useState, type ElementType } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileCode,
  Folder,
  FolderOpen,
  ListTree,
} from "lucide-react";
import { cn } from "../lib/cn";
import { formatNumber, truncatePath } from "../lib/format";
import { EmptyState } from "../shared/empty-state";
import { Skeleton } from "../ui/skeleton";
import { NodeBadges } from "./node-badges";
import { isExternal } from "@repowise-dev/types";
import type { GraphExport, GraphLink, GraphNode } from "@repowise-dev/types/graph";

/**
 * The file -> file edge types that mean "this file references that one", the
 * TypeScript mirror of the engine's `FILE_DEPENDENCY_EDGE_TYPES`
 * (`packages/core/src/repowise/core/ingestion/models.py`).
 *
 * `co_changes` is the one this deliberately drops, and it is the reason the
 * list exists at all: it joins files that were committed together, which is
 * history rather than code, and a tree whose per-file list mixed the two would
 * report a changelog as an import.
 */
export const CODE_GRAPH_DEPENDENCY_EDGE_TYPES: ReadonlySet<string> = new Set([
  "imports",
  "type_use",
  "framework",
  "dynamic_uses",
  "dynamic_imports",
  "dynamic_url_route",
  "reads",
]);

/**
 * Whether an edge belongs in a file's import list.
 *
 * An absent `edge_type` is admitted rather than dropped: the field is optional
 * on the wire for back-compat, and an index predating it carries real import
 * edges with nothing to classify them. Rendering them unnamed is honest;
 * hiding them is not.
 */
function isDependencyEdge(edgeType: string | null | undefined): boolean {
  return edgeType === undefined || edgeType === null || CODE_GRAPH_DEPENDENCY_EDGE_TYPES.has(edgeType);
}

/** Short labels for the edge kinds that are not a plain import. */
const EDGE_KIND_LABELS: Record<string, string> = {
  type_use: "type reference",
  framework: "framework wiring",
  dynamic_uses: "dynamic reference",
  dynamic_imports: "dynamic import",
  dynamic_url_route: "URL route",
  reads: "member read",
};

/** One file this file takes names from, with the names themselves. */
export interface CodeGraphImport {
  /** The file the names come from; may be an `external:` / `framework:` node. */
  target: string;
  edge_type: string | null;
  /** Sorted and deduped. Empty when the edge carries no names. */
  names: string[];
}

/** A file row: the node plus what it imports. */
export interface CodeGraphFile {
  node: GraphNode;
  imports: CodeGraphImport[];
}

/** One row of the outline: a directory, or a file. */
export interface CodeGraphTreeItem {
  /** A directory path, or the file's `node_id`. */
  id: string;
  /** Basename, for display. Directories keep their own segment. */
  name: string;
  isDir: boolean;
  children: CodeGraphTreeItem[];
  /** Files at or below this row (1 for a file row). */
  fileCount: number;
  /** Present on file rows only. */
  file?: CodeGraphFile;
}

/**
 * Fold the payload's links into one import list per source file.
 *
 * Parallel edges are merged rather than listed twice: the engine already
 * aggregates `imported_names` onto a single edge per pair, but a payload from
 * another producer may not, and two rows for one target reads as two separate
 * dependencies. Both endpoints must be nodes in the payload, so the list never
 * names a file the tree cannot show. External targets are kept, since
 * `external:react` is exactly what a lot of files import.
 */
function collectImports(
  nodes: readonly GraphNode[],
  links: readonly GraphLink[],
): Map<string, CodeGraphImport[]> {
  const nodeIds = new Set(nodes.map((n) => n.node_id));
  const bySource = new Map<string, Map<string, { kind: string | null; names: Set<string> }>>();
  for (const link of links) {
    if (link.source === link.target) continue;
    if (!nodeIds.has(link.source) || !nodeIds.has(link.target)) continue;
    if (!isDependencyEdge(link.edge_type)) continue;
    let targets = bySource.get(link.source);
    if (!targets) {
      targets = new Map();
      bySource.set(link.source, targets);
    }
    // An edge type is stamped when the edge is created and never rewritten
    // (a stronger `imports` edge keeps its kind when a `type_use` pass merges
    // into it), so first-wins for the kind is the engine's own answer.
    const existing = targets.get(link.target);
    if (existing) {
      for (const name of link.imported_names ?? []) existing.names.add(name);
    } else {
      targets.set(link.target, {
        kind: link.edge_type ?? null,
        names: new Set(link.imported_names ?? []),
      });
    }
  }

  const imports = new Map<string, CodeGraphImport[]>();
  for (const [source, targets] of bySource) {
    imports.set(
      source,
      [...targets.entries()]
        .map(([target, { kind, names }]) => ({
          target,
          edge_type: kind,
          names: [...names].sort(),
        }))
        .sort((a, b) => a.target.localeCompare(b.target)),
    );
  }
  return imports;
}

/**
 * Build the outline from a file-level graph payload.
 *
 * Directories are derived from the paths, not from the payload: there is no
 * directory node to read, and a tree that invented a hierarchy the graph does
 * not carry would be describing something other than the files that are there.
 *
 * `external:` / `framework:` nodes share the node table with real files and are
 * not files: they have no path, so they would land at the root as a row nobody
 * can open. They are skipped as rows and kept as import targets.
 */
export function buildCodeGraphTree(
  nodes: readonly GraphNode[],
  links: readonly GraphLink[],
): CodeGraphTreeItem[] {
  const imports = collectImports(nodes, links);
  const dirs = new Map<string, CodeGraphTreeItem>();
  const root: CodeGraphTreeItem[] = [];

  function ensureDir(dirPath: string): CodeGraphTreeItem {
    const existing = dirs.get(dirPath);
    if (existing) return existing;
    const parts = dirPath.split("/");
    const node: CodeGraphTreeItem = {
      id: dirPath,
      name: parts[parts.length - 1] ?? dirPath,
      isDir: true,
      children: [],
      fileCount: 0,
    };
    dirs.set(dirPath, node);
    if (parts.length > 1) {
      const parent = ensureDir(parts.slice(0, -1).join("/"));
      if (!parent.children.some((child) => child.id === dirPath)) parent.children.push(node);
    } else {
      root.push(node);
    }
    return node;
  }

  for (const node of nodes) {
    if (isExternal(node.node_id)) continue;
    const parts = node.node_id.split("/");
    const file: CodeGraphTreeItem = {
      id: node.node_id,
      name: parts[parts.length - 1] ?? node.node_id,
      isDir: false,
      children: [],
      fileCount: 1,
      file: { node, imports: imports.get(node.node_id) ?? [] },
    };
    if (parts.length > 1) {
      ensureDir(parts.slice(0, -1).join("/")).children.push(file);
    } else {
      root.push(file);
    }
  }

  /** Directories first, then files, each alphabetically, at every level. */
  function sortAndCount(items: CodeGraphTreeItem[]): number {
    items.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
    });
    let count = 0;
    for (const item of items) {
      item.fileCount = item.isDir ? sortAndCount(item.children) : 1;
      count += item.fileCount;
    }
    return count;
  }
  sortAndCount(root);
  return root;
}

/** What a file row says about its imports, as figures rather than prose. */
export interface FileImportSummary {
  /** Distinct files this file takes names from. */
  fileCount: number;
  /** Distinct names across those edges. */
  nameCount: number;
}

export function summarizeFileImports(imports: readonly CodeGraphImport[]): FileImportSummary {
  const names = new Set<string>();
  for (const entry of imports) for (const name of entry.names) names.add(name);
  return { fileCount: imports.length, nameCount: names.size };
}

/** The one-line count for a file row. Says which half is empty rather than
 *  printing a figure that describes nothing. */
function importSummaryLabel(summary: FileImportSummary): string {
  const { fileCount, nameCount } = summary;
  if (fileCount === 0) return "no dependencies";
  const files = `${fileCount} ${fileCount === 1 ? "file" : "files"}`;
  if (nameCount === 0) return `${files}, no names recorded`;
  return `${nameCount} ${nameCount === 1 ? "type" : "types"} from ${files}`;
}

/** Every directory id in the outline, for expand/collapse-all. */
export function collectDirectoryIds(items: readonly CodeGraphTreeItem[]): string[] {
  const ids: string[] = [];
  const walk = (list: readonly CodeGraphTreeItem[]) => {
    for (const item of list) {
      if (!item.isDir) continue;
      ids.push(item.id);
      walk(item.children);
    }
  };
  walk(items);
  return ids;
}

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

interface RowChrome {
  fileHref?: ((path: string) => string) | undefined;
  LinkComponent: ElementType;
}

function ImportList({ entry, chrome }: { entry: CodeGraphFile; chrome: RowChrome }) {
  if (entry.imports.length === 0) {
    return (
      <p className="py-1 pl-2 text-xs text-[var(--color-text-tertiary)]">
        No dependency edges recorded for this file. Files land in the graph once their
        imports are parsed, so an excluded path or an unsupported language leaves this empty.
      </p>
    );
  }
  const A = chrome.LinkComponent;
  return (
    <ul className="mb-1 space-y-1.5 border-l border-[var(--color-border-default)] pl-4">
      {entry.imports.map((imp) => {
        const kindLabel = imp.edge_type ? EDGE_KIND_LABELS[imp.edge_type] : undefined;
        // An external node has no page behind it, so it gets no link: the same
        // rule the file page's graph tab follows.
        const href = isExternal(imp.target) ? undefined : chrome.fileHref?.(imp.target);
        return (
          <li key={imp.target}>
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span
                className="font-mono text-[11px] text-[var(--color-text-primary)]"
                title={imp.target}
              >
                {truncatePath(imp.target, 64)}
              </span>
              {kindLabel && (
                <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                  {kindLabel}
                </span>
              )}
              {href && (
                <A
                  href={href}
                  className="text-[10px] text-[var(--color-accent-primary)] hover:underline"
                  aria-label={`Open ${imp.target}`}
                >
                  open <span aria-hidden>→</span>
                </A>
              )}
            </div>
            {imp.names.length > 0 ? (
              <p className="font-mono text-[11px] leading-relaxed text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
                {imp.names.join(", ")}
              </p>
            ) : (
              <p className="text-[11px] text-[var(--color-text-tertiary)]">
                The edge records no names, so we know these two files are joined and not what
                travelled along it.
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function FileRow({
  item,
  depth,
  expanded,
  toggle,
  chrome,
}: {
  item: CodeGraphTreeItem;
  depth: number;
  expanded: ReadonlySet<string>;
  toggle: (id: string) => void;
  chrome: RowChrome;
}) {
  const file = item.file;
  if (!file) return null;
  const isOpen = expanded.has(item.id);
  const summary = summarizeFileImports(file.imports);
  const href = chrome.fileHref?.(item.id);
  const A = chrome.LinkComponent;
  const listId = `code-graph-tree-imports-${item.id}`;

  return (
    <li>
      <div
        className="flex items-center gap-2 rounded-md pr-2 hover:bg-[var(--color-bg-elevated)]"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        <button
          type="button"
          onClick={() => toggle(item.id)}
          aria-expanded={isOpen}
          aria-controls={listId}
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md py-1.5 text-left text-xs text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          {isOpen ? (
            <ChevronDown className="h-3 w-3 shrink-0 opacity-50" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 opacity-50" />
          )}
          <FileCode className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
          <span
            className="min-w-0 truncate font-mono text-xs text-[var(--color-text-primary)]"
            title={item.id}
          >
            {item.name}
          </span>
          <NodeBadges
            signals={{ isTest: file.node.is_test, isEntryPoint: file.node.is_entry_point }}
            only={["isTest", "isEntryPoint"]}
          />
        </button>
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
          {importSummaryLabel(summary)}
        </span>
        {href && (
          <A
            href={href}
            className="shrink-0 text-[10px] text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)] hover:underline"
            aria-label={`Open ${item.id}`}
          >
            file <span aria-hidden>→</span>
          </A>
        )}
      </div>
      {isOpen && (
        <div id={listId} style={{ paddingLeft: `${depth * 16 + 24}px` }} className="pb-1">
          <ImportList entry={file} chrome={chrome} />
        </div>
      )}
    </li>
  );
}

function DirRow({
  item,
  depth,
  expanded,
  toggle,
  chrome,
}: {
  item: CodeGraphTreeItem;
  depth: number;
  expanded: ReadonlySet<string>;
  toggle: (id: string) => void;
  chrome: RowChrome;
}) {
  const isOpen = expanded.has(item.id);
  return (
    <li>
      <button
        type="button"
        onClick={() => toggle(item.id)}
        aria-expanded={isOpen}
        className={cn(
          "flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left text-xs transition-colors hover:bg-[var(--color-bg-elevated)]",
          depth === 0
            ? "font-semibold text-[var(--color-text-primary)]"
            : "text-[var(--color-text-secondary)]",
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {isOpen ? (
          <ChevronDown className="h-3 w-3 shrink-0 opacity-50" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 opacity-50" />
        )}
        {isOpen ? (
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)] opacity-70" />
        ) : (
          <Folder className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
        )}
        <span className="min-w-0 flex-1 truncate font-mono">{item.name}</span>
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
          {formatNumber(item.fileCount)} {item.fileCount === 1 ? "file" : "files"}
        </span>
      </button>
      {isOpen && (
        <ul>
          {item.children.map((child) => (
            <TreeRow
              key={child.id}
              item={child}
              depth={depth + 1}
              expanded={expanded}
              toggle={toggle}
              chrome={chrome}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function TreeRow({
  item,
  depth,
  expanded,
  toggle,
  chrome,
}: {
  item: CodeGraphTreeItem;
  depth: number;
  expanded: ReadonlySet<string>;
  toggle: (id: string) => void;
  chrome: RowChrome;
}) {
  return item.isDir ? (
    <DirRow item={item} depth={depth} expanded={expanded} toggle={toggle} chrome={chrome} />
  ) : (
    <FileRow item={item} depth={depth} expanded={expanded} toggle={toggle} chrome={chrome} />
  );
}

// ---------------------------------------------------------------------------
// The tree
// ---------------------------------------------------------------------------

export interface CodeGraphTreeProps {
  /** The file-level graph payload. `undefined` while it is still arriving. */
  graph: GraphExport | null | undefined;
  /** Renders the skeleton instead of the empty state while the first fetch runs. */
  isLoading?: boolean | undefined;
  /** Builds a file entity page href. Rows render no link when it is omitted. */
  fileHref?: ((path: string) => string) | undefined;
  /** Router link; defaults to `<a>` so the package stays framework-neutral. */
  LinkComponent?: ElementType | undefined;
  className?: string | undefined;
}

export function CodeGraphTree({
  graph,
  isLoading = false,
  fileHref,
  LinkComponent = "a",
  className,
}: CodeGraphTreeProps) {
  const tree = useMemo(
    () => (graph ? buildCodeGraphTree(graph.nodes, graph.links) : []),
    [graph],
  );
  // Everything starts shut. The top rung on a real repo is dozens of
  // directories and thousands of files, and a first screen that is already
  // open to depth three is a wall rather than an outline. `Expand all` is one
  // click away for the reader who wants the whole shape.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const chrome: RowChrome = { fileHref, LinkComponent };

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const directoryIds = useMemo(() => collectDirectoryIds(tree), [tree]);
  const totalFiles = useMemo(
    () => tree.reduce((sum, item) => sum + item.fileCount, 0),
    [tree],
  );

  if (isLoading && !graph) {
    return (
      <div className={cn("space-y-2", className)} aria-label="Loading the code graph tree">
        <Skeleton className="h-4 w-64" />
        {Array.from({ length: 10 }).map((_, index) => (
          <Skeleton key={index} className="h-7 w-full" />
        ))}
      </div>
    );
  }

  if (tree.length === 0) {
    return (
      <EmptyState
        titleAs="h2"
        icon={<ListTree className="h-8 w-8" />}
        title="No files to outline"
        description="Files land in the graph once their imports are parsed. An empty index, or one whose files are all third-party nodes, leaves this with nothing to draw."
        {...(className ? { className } : {})}
      />
    );
  }

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-secondary)]">
        <p className="min-w-0 flex-1">
          <span className="font-medium tabular-nums text-[var(--color-text-primary)]">
            {formatNumber(totalFiles)}
          </span>{" "}
          {totalFiles === 1 ? "file" : "files"} in{" "}
          <span className="font-medium tabular-nums text-[var(--color-text-primary)]">
            {formatNumber(directoryIds.length)}
          </span>{" "}
          {directoryIds.length === 1 ? "directory" : "directories"}. Open a file to see the types
          it imports and where each one comes from.
        </p>
        <button
          type="button"
          onClick={() => setExpanded(new Set(directoryIds))}
          className="shrink-0 font-medium text-[var(--color-accent-primary)] hover:underline"
        >
          Expand all
        </button>
        <button
          type="button"
          onClick={() => setExpanded(new Set())}
          className="shrink-0 font-medium text-[var(--color-accent-primary)] hover:underline"
        >
          Collapse all
        </button>
      </div>
      <ul className="space-y-0.5">
        {tree.map((item) => (
          <TreeRow
            key={item.id}
            item={item}
            depth={0}
            expanded={expanded}
            toggle={toggle}
            chrome={chrome}
          />
        ))}
      </ul>
    </div>
  );
}
