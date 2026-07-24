"use client";

import { useEffect, useState, useMemo } from "react";
import {
  ChevronDown,
  ChevronRight,
  Compass,
  FolderOpen,
  Folder,
  Search,
  Filter,
  FolderTree,
  Network,
} from "lucide-react";
import {
  ALL_PAGE_TYPES,
  ONBOARDING_SLOT_TITLES,
  getOnboardingSlot,
  getPageTypeIcon,
  getPageTypeLabel,
  type OnboardingSlot,
} from "../lib/page-types";
import { RAW_GRAPH_ID, displayLabel, treeLabel } from "./page-labels";
import { cn } from "../lib/cn";
import { statusBadgeClasses, type FreshnessStatus } from "../lib/confidence";
import type { DocPage } from "@repowise-dev/types/docs";

// Synthetic path used as the Onboarding folder's tree key. Distinct from any
// real target_path (which never starts with "@") so directory lookups don't
// collide with module paths.
const ONBOARDING_DIR_KEY = "@onboarding";
// Tree expansion survives reloads (per-browser, not per-repo — paths rarely
// collide across repos and the fallback is just the default expansion).
const EXPANDED_DIRS_KEY = "repowise:docs-tree-expanded";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TreeNode {
  name: string;
  path: string;
  isDir: boolean;
  page?: DocPage;
  children: TreeNode[];
  /** Dotted outline number from the stored tree ("2.4.1"), when the page has one. */
  section?: string;
}

interface DocsTreeProps {
  pages: DocPage[];
  selectedPageId: string | null;
  onSelectPage: (page: DocPage) => void;
  className?: string;
}

// ---------------------------------------------------------------------------
// Page type icons
// ---------------------------------------------------------------------------

function PageIcon({ pageType, className }: { pageType: string; className?: string }) {
  const Icon = getPageTypeIcon(pageType);
  return <Icon {...(className ? { className } : {})} />;
}

// ---------------------------------------------------------------------------
// Build tree from flat page list
// ---------------------------------------------------------------------------

function buildOnboardingFolder(pages: DocPage[]): TreeNode | null {
  // Bucket every page by its onboarding slot. Both promoted pages
  // (repo_overview / architecture_diagram, tagged via metadata) and dedicated
  // `page_type === "onboarding"` pages flow into the same bucket.
  const bySlot = new Map<OnboardingSlot, DocPage>();
  for (const page of pages) {
    const slot = getOnboardingSlot(page);
    if (slot && !bySlot.has(slot)) {
      bySlot.set(slot, page);
    }
  }
  if (bySlot.size === 0) return null;

  // Reading order comes from the stored tree (`display_order`, assigned once
  // at generation time), not from a slot list duplicated in TypeScript. A
  // store written before the tree existed has every order at 0; the title
  // tiebreak keeps that case stable rather than dependent on map insertion.
  const children: TreeNode[] = [...bySlot.entries()]
    .sort(
      ([, a], [, b]) =>
        (a.display_order ?? 0) - (b.display_order ?? 0) || a.title.localeCompare(b.title),
    )
    .map(([slot, page]) => ({
      name: ONBOARDING_SLOT_TITLES[slot],
      path: page.id,
      isDir: false,
      page,
      children: [],
    }));

  return {
    name: "Onboarding",
    path: ONBOARDING_DIR_KEY,
    isDir: true,
    children,
  };
}

function buildTree(pages: DocPage[]): TreeNode[] {
  const root: TreeNode[] = [];

  // ---- Onboarding folder (always at top when any slot is filled) ----
  const onboardingFolder = buildOnboardingFolder(pages);
  if (onboardingFolder) {
    root.push(onboardingFolder);
  }

  // Pages already shown inside the Onboarding folder are skipped at the
  // top level so they don't appear twice.
  const onboardingPageIds = new Set(
    onboardingFolder
      ? onboardingFolder.children.map((c) => c.page?.id).filter((id): id is string => Boolean(id))
      : [],
  );

  // ---- Remaining special pages (overview/architecture only when *not*
  // already promoted into the Onboarding folder) and path-based pages ----
  const specialPages: DocPage[] = [];
  const pathPages: DocPage[] = [];

  for (const page of pages) {
    if (onboardingPageIds.has(page.id)) continue;
    // Dedicated onboarding pages without a recognised slot fall through to
    // path-based grouping under the "onboarding/" prefix.
    if (page.page_type === "repo_overview" || page.page_type === "architecture_diagram") {
      specialPages.push(page);
    } else {
      pathPages.push(page);
    }
  }

  // Add remaining special pages at top level
  for (const page of specialPages) {
    root.push({
      name: page.title,
      path: page.id,
      isDir: false,
      page,
      children: [],
    });
  }

  // Build directory tree from path-based pages
  const dirMap = new Map<string, TreeNode>();

  function ensureDir(dirPath: string): TreeNode {
    if (dirMap.has(dirPath)) return dirMap.get(dirPath)!;

    const parts = dirPath.split("/");
    const name = parts[parts.length - 1] ?? dirPath;
    const node: TreeNode = {
      name,
      path: dirPath,
      isDir: true,
      children: [],
    };
    dirMap.set(dirPath, node);

    if (parts.length > 1) {
      const parentPath = parts.slice(0, -1).join("/");
      const parent = ensureDir(parentPath);
      // Only add if not already a child
      if (!parent.children.some((c) => c.path === dirPath)) {
        parent.children.push(node);
      }
    }

    return node;
  }

  // Check if any path page is a module_page that matches a directory
  const modulePaths = new Set(
    pathPages.filter((p) => p.page_type === "module_page").map((p) => p.target_path),
  );

  for (const page of pathPages) {
    const targetPath = page.target_path;
    if (!targetPath) continue;

    if (page.page_type === "module_page") {
      // Module pages become directories with their page attached. Community
      // modules have synthetic target_paths ("community-207") — show the
      // derived module name instead of the raw graph id.
      const dirNode = ensureDir(targetPath);
      dirNode.page = page;
      if (RAW_GRAPH_ID.test(dirNode.name)) dirNode.name = displayLabel(page);
    } else {
      // File pages go into their parent directory, named by their basename.
      // Cycle pages have a synthetic path ("scc-103") that reads as noise, so
      // they use their derived label ("Cycle: generation/page_generator")
      // instead, the same name the concept tree gives them.
      const parts = targetPath.split("/");
      const fileName =
        page.page_type === "scc_page"
          ? displayLabel(page)
          : (parts[parts.length - 1] ?? targetPath);

      const fileNode: TreeNode = {
        name: fileName,
        path: page.id,
        isDir: false,
        page,
        children: [],
      };

      if (parts.length > 1) {
        const parentPath = parts.slice(0, -1).join("/");
        const parent = ensureDir(parentPath);
        parent.children.push(fileNode);
      } else {
        root.push(fileNode);
      }
    }
  }

  // Add top-level directories to root
  for (const [dirPath, node] of dirMap) {
    if (!dirPath.includes("/")) {
      root.push(node);
    }
  }

  // Sort children: directories first, then files, both alphabetically
  function sortChildren(nodes: TreeNode[]) {
    nodes.sort((a, b) => {
      if (a.isDir && !b.isDir) return -1;
      if (!a.isDir && b.isDir) return 1;
      return a.name.localeCompare(b.name);
    });
    for (const node of nodes) {
      if (node.children.length > 0) sortChildren(node.children);
    }
  }

  sortChildren(root);

  // The Onboarding folder is a fixed top-level entry and must appear first,
  // regardless of alphabetical order against other directories.
  const onbIdx = root.findIndex((n) => n.path === ONBOARDING_DIR_KEY);
  if (onbIdx > 0) {
    const [onbNode] = root.splice(onbIdx, 1);
    if (onbNode) root.unshift(onbNode);
  }

  return root;
}

// ---------------------------------------------------------------------------
// Domain (semantic) tree — read from the store, not derived here
// ---------------------------------------------------------------------------
//
// The hierarchy used to be rebuilt in this file: a hardcoded four-section
// spine, a majority vote over each module's files to guess its layer, and
// longest-prefix path matching to guess which module owned a file. It is now
// computed once at generation time (`core/generation/page_tree.py`) and stored
// on every page as parent_page_id / display_order / section_number, so this
// component, the editor extension and the MCP server all read one outline
// instead of each deriving a different one.
//
// Shape on a repo with a curated knowledge graph:
//
//   Repository Overview        the root, rendered first
//   1   onboarding pages       canonical reading order
//   7   architecture diagram
//   8   layer pages            dependency spine
//   8.1   module pages         → file pages → symbol spotlights
//   8.9   cycle pages
//
// A rung the repo has no pages for simply does not appear.

// Bucket key for a run of same-type siblings, and for pages the stored tree
// does not reach. Namespaced like the other synthetic keys so it can never
// collide with a real page id; the parent id is part of the key so two
// parents' buckets of the same type expand independently.
const TYPE_GROUP_PREFIX = "@group:type:";

const typeGroupKey = (parentId: string, pageType: string) =>
  `${TYPE_GROUP_PREFIX}${parentId}:${pageType}`;

// Unreachable pages are bucketed under the empty parent, and those buckets
// open by default: on a store whose tree has never been built they are the
// whole tree, so leaving them shut would show almost nothing.
const STRAY_GROUP_KEYS = ALL_PAGE_TYPES.map((t) => typeGroupKey("", t));

// The page types that form the navigable concept spine — the model-written
// outline a human reads. Everything else is a deterministic, structural page
// (files, symbols, cycles, API contracts, infra): the per-file reference an
// agent looks things up in. The two are kept as distinct surfaces. Only the
// spine is walked into a hierarchy; every deterministic page goes into one
// collapsed folder at the very bottom, so the outline above it stays clean.
const SPINE_TYPES = new Set([
  "repo_overview",
  "architecture_diagram",
  "layer_page",
  "module_page",
  "onboarding",
]);

const isSpinePage = (page: DocPage) => SPINE_TYPES.has(page.page_type);

// The single bottom folder holding every deterministic page. Namespaced like
// the other synthetic keys so it can never collide with a real page id.
const AUTO_ROOT_KEY = "@group:auto-documented";

function compareSiblings(a: DocPage, b: DocPage): number {
  return (
    (a.display_order ?? 0) - (b.display_order ?? 0) ||
    (a.target_path || a.title).localeCompare(b.target_path || b.title)
  );
}

function buildStoredTree(pages: DocPage[]): TreeNode[] {
  // A tombstoned page documents a file that no longer exists. It keeps its row
  // and its content, but the tree deliberately has no place for it, so it must
  // be excluded here rather than treated as an unplaced page.
  const visible = pages.filter((p) => p.freshness_status !== "tombstone");

  // Two distinct surfaces. The spine is walked into the concept outline; every
  // deterministic page is set aside for the single bottom folder, so a file
  // page never appears in the outline itself.
  const spinePages = visible.filter(isSpinePage);
  const deterministicPages = visible.filter((p) => !isSpinePage(p));

  const byId = new Map(spinePages.map((p) => [p.id, p]));
  const childrenOf = new Map<string, DocPage[]>();
  const claimed = new Set<string>();
  for (const page of spinePages) {
    const parentId = page.parent_page_id;
    if (!parentId || parentId === page.id || !byId.has(parentId)) continue;
    const bucket = childrenOf.get(parentId);
    if (bucket) bucket.push(page);
    else childrenOf.set(parentId, [page]);
    claimed.add(page.id);
  }

  // The root is the concept page nothing claims that other pages hang off. A
  // store written before the tree existed has no such page — every parent is
  // null — and falls through to the grouped tail below.
  const rootCandidates = spinePages.filter((p) => !claimed.has(p.id) && childrenOf.has(p.id));
  const root =
    rootCandidates.find((p) => p.page_type === "repo_overview") ?? rootCandidates[0] ?? null;

  // Reached, not just claimed: a parent cycle would otherwise silently swallow
  // every page in it. Anything the walk misses lands in the tail instead.
  const reached = new Set<string>();
  function toNode(page: DocPage, parent: DocPage | undefined): TreeNode {
    reached.add(page.id);
    const children = (childrenOf.get(page.id) ?? [])
      .filter((c) => !reached.has(c.id))
      .sort(compareSiblings)
      .map((c) => toNode(c, page));
    return {
      name: treeLabel(page, parent),
      path: page.id,
      isDir: children.length > 0,
      page,
      children,
      ...(page.section_number ? { section: page.section_number } : {}),
    };
  }

  const top: TreeNode[] = [];
  if (root) {
    reached.add(root.id);
    top.push({
      name: treeLabel(root, undefined),
      path: root.id,
      isDir: false,
      page: root,
      children: [],
    });
    top.push(
      ...(childrenOf.get(root.id) ?? [])
        .slice()
        .sort(compareSiblings)
        .map((child) => toNode(child, root)),
    );
  }

  // Concept pages the walk never reached. Grouped by type rather than dropped:
  // an unplaced page is still a page. On a store whose tree has not been built
  // yet this grouping IS the outline, a fair rendering of a wiki that genuinely
  // has no recorded hierarchy.
  const strayByType = new Map<string, DocPage[]>();
  for (const page of spinePages) {
    if (reached.has(page.id)) continue;
    const bucket = strayByType.get(page.page_type);
    if (bucket) bucket.push(page);
    else strayByType.set(page.page_type, [page]);
  }
  const orderedTypes = [
    ...ALL_PAGE_TYPES.filter((t) => strayByType.has(t)),
    ...[...strayByType.keys()].filter((t) => !ALL_PAGE_TYPES.includes(t)).sort(),
  ];
  for (const type of orderedTypes) {
    const group = strayByType.get(type)!;
    top.push({
      name: `${getPageTypeLabel(type)} (${group.length})`,
      path: typeGroupKey("", type),
      isDir: true,
      children: group.sort(compareSiblings).map((p) => ({
        name: treeLabel(p, undefined),
        path: p.id,
        isDir: false,
        page: p,
        children: [],
      })),
    });
  }

  // Every deterministic page in one collapsed folder at the very bottom, held
  // apart from the concept outline so the distinction is obvious. Its interior
  // reuses the filesystem builder, so the files stay navigable by directory.
  if (deterministicPages.length > 0) {
    top.push({
      name: `Auto-documented files (${deterministicPages.length})`,
      path: AUTO_ROOT_KEY,
      isDir: true,
      children: buildTree(deterministicPages),
    });
  }

  return top;
}

// ---------------------------------------------------------------------------
// Filter helpers
// ---------------------------------------------------------------------------

type TypeFilter = "all" | typeof ALL_PAGE_TYPES[number];
type FreshnessFilter = "all" | "fresh" | "stale" | "outdated";

function matchesFilters(
  page: DocPage | undefined,
  search: string,
  typeFilter: TypeFilter,
  freshnessFilter: FreshnessFilter,
  displayName?: string,
): boolean {
  if (!page) return true; // directories always pass (will be pruned if empty)
  if (typeFilter !== "all" && page.page_type !== typeFilter) return false;
  if (freshnessFilter !== "all" && page.freshness_status !== freshnessFilter) return false;
  if (search) {
    const q = search.toLowerCase();
    return (
      page.title.toLowerCase().includes(q) ||
      page.target_path.toLowerCase().includes(q) ||
      // Derived tree labels (e.g. "Cycle: generation/page_generator") are
      // what the user sees — make them searchable too.
      (displayName ?? "").toLowerCase().includes(q)
    );
  }
  return true;
}

function filterTree(
  nodes: TreeNode[],
  search: string,
  typeFilter: TypeFilter,
  freshnessFilter: FreshnessFilter,
): TreeNode[] {
  const result: TreeNode[] = [];
  for (const node of nodes) {
    if (node.isDir) {
      const filteredChildren = filterTree(node.children, search, typeFilter, freshnessFilter);
      const dirPageMatches = node.page
        ? matchesFilters(node.page, search, typeFilter, freshnessFilter, node.name)
        : false;
      if (filteredChildren.length > 0 || dirPageMatches) {
        result.push({ ...node, children: filteredChildren });
      }
    } else {
      if (matchesFilters(node.page, search, typeFilter, freshnessFilter, node.name)) {
        result.push(node);
      }
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Tree node component
// ---------------------------------------------------------------------------

function TreeItem({
  node,
  depth,
  selectedPageId,
  expandedDirs,
  toggleDir,
  onSelectPage,
  forceExpand = false,
  showFreshness = false,
}: {
  node: TreeNode;
  depth: number;
  selectedPageId: string | null;
  expandedDirs: Set<string>;
  toggleDir: (path: string) => void;
  onSelectPage: (page: DocPage) => void;
  /** Open every dir while a search is active so matches are never hidden. */
  forceExpand?: boolean;
  /** Per-row freshness dots are opt-in — off by default to keep rows quiet. */
  showFreshness?: boolean;
}) {
  const isExpanded = forceExpand || expandedDirs.has(node.path);
  const isSelected = node.page && node.page.id === selectedPageId;
  const hasChildren = node.children.length > 0;

  if (node.isDir) {
    return (
      <div>
        <button
          onClick={() => {
            toggleDir(node.path);
            if (node.page) onSelectPage(node.page);
          }}
          {...(node.page && node.page.title !== node.name ? { title: node.page.title } : {})}
          className={cn(
            "flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-bg-elevated)]",
            isSelected
              ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
              : "text-[var(--color-text-secondary)]",
          )}
          style={{ paddingLeft: `${depth * 16 + 8}px` }}
        >
          {hasChildren ? (
            isExpanded ? (
              <ChevronDown className="h-3 w-3 shrink-0 opacity-50" />
            ) : (
              <ChevronRight className="h-3 w-3 shrink-0 opacity-50" />
            )
          ) : (
            <span className="w-3 shrink-0" />
          )}
          <SectionNumber depth={depth} section={node.section} />
          {node.path === ONBOARDING_DIR_KEY ? (
            <Compass className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)]" />
          ) : node.page ? (
            // A dir that is itself a page (layer, module, cycle) keeps its page
            // type's icon — a folder glyph would say less than the type does.
            <PageIcon
              pageType={node.page.page_type}
              className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)]"
            />
          ) : isExpanded ? (
            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)] opacity-70" />
          ) : (
            <Folder className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
          )}
          <span
            className={cn(
              "truncate font-medium",
              (node.path === ONBOARDING_DIR_KEY || depth === 0) &&
                "text-[var(--color-text-primary)]",
            )}
          >
            {node.name}
          </span>
          {showFreshness && node.page && (
            <FreshnessDot status={node.page.freshness_status as FreshnessStatus} />
          )}
        </button>

        {isExpanded && hasChildren && (
          <div>
            {node.children.map((child) => (
              <TreeItem
                key={child.path}
                node={child}
                depth={depth + 1}
                selectedPageId={selectedPageId}
                expandedDirs={expandedDirs}
                toggleDir={toggleDir}
                onSelectPage={onSelectPage}
                forceExpand={forceExpand}
                showFreshness={showFreshness}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  // File/leaf node
  return (
    <button
      onClick={() => node.page && onSelectPage(node.page)}
      {...(node.page && node.page.title !== node.name ? { title: node.page.title } : {})}
      className={cn(
        "flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-bg-elevated)]",
        isSelected
          ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
          : "text-[var(--color-text-secondary)]",
      )}
      style={{ paddingLeft: `${depth * 16 + 8 + 16}px` }}
    >
      <SectionNumber depth={depth} section={node.section} />
      <PageIcon
        pageType={node.page?.page_type ?? "file_page"}
        className={cn(
          "h-3.5 w-3.5 shrink-0",
          isSelected ? "text-[var(--color-accent-primary)]" : "text-[var(--color-text-tertiary)]",
        )}
      />
      <span className="truncate">{node.name}</span>
      {showFreshness && node.page && (
        <FreshnessDot status={node.page.freshness_status as FreshnessStatus} />
      )}
    </button>
  );
}

// The stored dotted number, shown on the top rung only. Deeper rows are
// already placed by indentation, and "14.3.2" on every file row is noise.
function SectionNumber({ depth, section }: { depth: number; section?: string | undefined }) {
  if (depth !== 0 || !section) return null;
  return (
    <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-tertiary)] tabular-nums">
      {section}
    </span>
  );
}

function FreshnessDot({ status }: { status: FreshnessStatus }) {
  // Fresh is the expected state — only flag pages that need attention, so a
  // healthy tree stays visually quiet instead of showing hundreds of dots.
  if (status === "fresh") return null;
  const color =
    status === "stale" ? "bg-[var(--color-warning)]" : "bg-[var(--color-error)]";
  return <span className={cn("ml-auto h-1.5 w-1.5 rounded-full shrink-0", color)} />;
}

// ---------------------------------------------------------------------------
// Main DocsTree component
// ---------------------------------------------------------------------------

type ViewMode = "domain" | "folder";

export function DocsTree({ pages, selectedPageId, onSelectPage, className }: DocsTreeProps) {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [freshnessFilter, setFreshnessFilter] = useState<FreshnessFilter>("all");
  // Default to the semantic "By domain" spine — overview/architecture/modules
  // first, filesystem second. The folder view is a toggle for power users.
  const [viewMode, setViewMode] = useState<ViewMode>("domain");
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(() => {
    // Auto-expand first two levels, the Onboarding folder, and the by-type
    // buckets that hold pages the stored tree does not reach (on a store whose
    // tree has not been built yet, those buckets are the whole tree, so
    // leaving them collapsed would show almost nothing). Then ADD any
    // previously expanded dirs from localStorage. Union (not replace) — the
    // key is shared across repos, so a stale saved set must never collapse
    // another repo's default-open rows.
    const dirs = new Set<string>(STRAY_GROUP_KEYS);
    dirs.add(ONBOARDING_DIR_KEY);
    // Domain view: open the section spine (the layers) so the concept titles
    // read as a clean table of contents on load. Everything else starts shut —
    // concept pages, the bottom Auto-documented folder, and the file directories
    // inside it — so the outline is what a reader sees first, with the files a
    // deliberate click away. A spine node opens by default only when it has a
    // spine child of its own (a layer holding concepts).
    const hasSpineChild = new Set(
      pages
        .filter((p) => SPINE_TYPES.has(p.page_type) && p.parent_page_id)
        .map((p) => p.parent_page_id as string),
    );
    for (const page of pages) {
      if (hasSpineChild.has(page.id) && SPINE_TYPES.has(page.page_type)) dirs.add(page.id);
    }
    if (typeof window !== "undefined") {
      try {
        const saved = window.localStorage.getItem(EXPANDED_DIRS_KEY);
        if (saved) for (const d of JSON.parse(saved) as string[]) dirs.add(d);
      } catch {
        // Corrupt state — defaults are fine.
      }
    }
    return dirs;
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(
        EXPANDED_DIRS_KEY,
        JSON.stringify([...expandedDirs]),
      );
    } catch {
      // Quota/SSR — persistence is best-effort.
    }
  }, [expandedDirs]);
  // Filters are a power-user affordance — start hidden so the panel opens
  // calm; the funnel button shows a count when any filter is active.
  const [showFilters, setShowFilters] = useState(false);
  // Per-row freshness dots are opt-in noise — off by default. Turning this on
  // (or filtering by status) is how a reader audits staleness across the tree.
  const [showFreshness, setShowFreshness] = useState(false);

  const tree = useMemo(
    () => (viewMode === "domain" ? buildStoredTree(pages) : buildTree(pages)),
    [pages, viewMode],
  );
  const filteredTree = useMemo(
    () => filterTree(tree, search, typeFilter, freshnessFilter),
    [tree, search, typeFilter, freshnessFilter],
  );

  const toggleDir = (path: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const totalPages = pages.length;
  const activeFilterCount =
    (typeFilter !== "all" ? 1 : 0) + (freshnessFilter !== "all" ? 1 : 0);

  return (
    <div className={cn("flex flex-col h-full", className)}>
      {/* Search + filter bar */}
      <div className="p-3 space-y-2 border-b border-[var(--color-border-default)]">
        <div className="flex items-center gap-2">
          <div className="flex-1 flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1.5">
            <Search className="h-3.5 w-3.5 text-[var(--color-text-tertiary)] shrink-0" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search docs..."
              className="flex-1 bg-transparent text-xs text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] outline-none"
            />
          </div>
          {/* View switch — domain is the default reading spine; folder is the
              power-user escape hatch, so it rides as a single quiet toggle
              rather than a full-width band. */}
          <button
            onClick={() => setViewMode((m) => (m === "domain" ? "folder" : "domain"))}
            aria-label={viewMode === "domain" ? "Switch to folder view" : "Switch to domain view"}
            title={viewMode === "domain" ? "Folder view" : "Domain view"}
            className="rounded-md p-1.5 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-secondary)]"
          >
            {viewMode === "domain" ? (
              <FolderTree className="h-3.5 w-3.5" />
            ) : (
              <Network className="h-3.5 w-3.5" />
            )}
          </button>
          <button
            onClick={() => setShowFilters((s) => !s)}
            aria-label="Toggle filters"
            aria-expanded={showFilters}
            className={cn(
              "relative rounded-md p-1.5 transition-colors",
              showFilters || activeFilterCount > 0
                ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                : "text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-secondary)]",
            )}
          >
            <Filter className="h-3.5 w-3.5" />
            {activeFilterCount > 0 && (
              <span className="absolute -top-1 -right-1 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-[var(--color-accent-fill)] text-[10px] font-semibold text-[var(--color-text-on-accent)]">
                {activeFilterCount}
              </span>
            )}
          </button>
        </div>

        {showFilters && (
          <div className="space-y-1.5">
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-[var(--color-text-tertiary)] uppercase tracking-wider font-medium w-10">Type</span>
              {(["all", ...ALL_PAGE_TYPES] as TypeFilter[]).map((t) => (
                <button
                  key={t}
                  onClick={() => setTypeFilter(t)}
                  className={cn(
                    "rounded-full px-2 py-0.5 text-[10px] border transition-colors",
                    typeFilter === t
                      ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                      : "border-[var(--color-border-default)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]",
                  )}
                >
                  {t === "all" ? "All" : getPageTypeLabel(t)}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-[var(--color-text-tertiary)] uppercase tracking-wider font-medium w-10">Status</span>
              {(["all", "fresh", "stale", "outdated"] as FreshnessFilter[]).map((f) => (
                <button
                  key={f}
                  onClick={() => setFreshnessFilter(f)}
                  className={cn(
                    "rounded-full px-2 py-0.5 text-[10px] border transition-colors",
                    freshnessFilter === f
                      ? f === "all"
                        ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                        : statusBadgeClasses(f as FreshnessStatus)
                      : "border-[var(--color-border-default)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]",
                  )}
                >
                  {f === "all" ? "All" : f.charAt(0).toUpperCase() + f.slice(1)}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1.5 text-[10px] text-[var(--color-text-tertiary)] cursor-pointer select-none">
              <input
                type="checkbox"
                checked={showFreshness}
                onChange={(e) => setShowFreshness(e.target.checked)}
                className="h-3 w-3 accent-[var(--color-accent-primary)]"
              />
              Show freshness dots on every row
            </label>
          </div>
        )}

        {/* Quiet page count. Staleness auditing lives in the Doc-freshness
            view and the Status filter, so the nav header stays calm. */}
        <div className="text-[10px] text-[var(--color-text-tertiary)]">{totalPages} pages</div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto p-1.5">
        {filteredTree.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-xs text-[var(--color-text-tertiary)]">
            <p>No matching pages</p>
          </div>
        ) : (
          <div className="space-y-1">
            {filteredTree.map((node) => (
              <TreeItem
                key={node.path}
                node={node}
                depth={0}
                selectedPageId={selectedPageId}
                expandedDirs={expandedDirs}
                toggleDir={toggleDir}
                onSelectPage={onSelectPage}
                forceExpand={search.trim().length > 0}
                showFreshness={showFreshness || freshnessFilter !== "all"}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
