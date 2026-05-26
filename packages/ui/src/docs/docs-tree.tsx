"use client";

import { useState, useMemo } from "react";
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
  Layers,
  BookOpen,
  FileText,
} from "lucide-react";
import {
  ALL_PAGE_TYPES,
  ONBOARDING_ORDER,
  ONBOARDING_SLOT_TITLES,
  getOnboardingSlot,
  getPageTypeIcon,
  getPageTypeLabel,
  type OnboardingSlot,
} from "../lib/page-types";
import { cn } from "../lib/cn";
import { statusBadgeClasses, type FreshnessStatus } from "../lib/confidence";
import type { DocPage } from "@repowise-dev/types/docs";

// Synthetic path used as the Onboarding folder's tree key. Distinct from any
// real target_path (which never starts with "@") so directory lookups don't
// collide with module paths.
const ONBOARDING_DIR_KEY = "@onboarding";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TreeNode {
  name: string;
  path: string;
  isDir: boolean;
  page?: DocPage;
  children: TreeNode[];
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

  // Render in canonical reading order. Slots without a page (gated out at
  // generation time) are silently skipped.
  const children: TreeNode[] = [];
  for (const slot of ONBOARDING_ORDER) {
    const page = bySlot.get(slot);
    if (!page) continue;
    children.push({
      name: ONBOARDING_SLOT_TITLES[slot],
      path: page.id,
      isDir: false,
      page,
      children: [],
    });
  }

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
      // Module pages become directories with their page attached
      const dirNode = ensureDir(targetPath);
      dirNode.page = page;
    } else {
      // File pages go into their parent directory
      const parts = targetPath.split("/");
      const fileName = parts[parts.length - 1] ?? targetPath;

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
// Domain (semantic) tree
// ---------------------------------------------------------------------------
//
// A narrative spine grouped by *meaning* rather than folder structure:
//
//   Guided Tour  → onboarding slots in canonical reading order
//   Architecture → layer / knowledge-graph / SCC pages (the zoom-out)
//   Modules      → each module_page with its files nested underneath
//   Reference    → loose files, symbols, API contracts, infra
//
// Section keys are namespaced with "@section:" so they never collide with a
// real target_path and get their own icon in TreeItem.

const SECTION_KEYS = {
  tour: "@section:tour",
  architecture: "@section:architecture",
  modules: "@section:modules",
  reference: "@section:reference",
} as const;

export const DOMAIN_SECTION_KEYS = Object.values(SECTION_KEYS);

function parentDirOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i === -1 ? "" : path.slice(0, i);
}

function buildDomainTree(pages: DocPage[]): TreeNode[] {
  const sections: TreeNode[] = [];

  // ---- Guided Tour (reuse the canonical onboarding ordering) ----
  const onboarding = buildOnboardingFolder(pages);
  const tourIds = new Set<string>();
  const tourChildren: TreeNode[] = onboarding ? [...onboarding.children] : [];
  for (const c of tourChildren) if (c.page) tourIds.add(c.page.id);

  // Guarantee the repo overview heads the tour even when it wasn't promoted
  // into an onboarding slot — it's the canonical front door.
  const overview = pages.find((p) => p.page_type === "repo_overview");
  if (overview && !tourIds.has(overview.id)) {
    tourChildren.unshift({
      name: overview.title || "Overview",
      path: overview.id,
      isDir: false,
      page: overview,
      children: [],
    });
    tourIds.add(overview.id);
  }

  if (tourChildren.length > 0) {
    sections.push({
      name: "Guided Tour",
      path: SECTION_KEYS.tour,
      isDir: true,
      children: tourChildren,
    });
  }

  // ---- Architecture (layers / knowledge graph / strongly-connected) ----
  const archTypes = new Set(["layer_page", "architecture_diagram", "scc_page"]);
  const archChildren: TreeNode[] = pages
    .filter((p) => archTypes.has(p.page_type) && !tourIds.has(p.id))
    .sort((a, b) => a.title.localeCompare(b.title))
    .map((p) => ({ name: p.title, path: p.id, isDir: false, page: p, children: [] }));
  if (archChildren.length > 0) {
    sections.push({
      name: "Architecture",
      path: SECTION_KEYS.architecture,
      isDir: true,
      children: archChildren,
    });
  }

  // ---- Modules (each module_page with the files it contains) ----
  const modulePages = pages
    .filter((p) => p.page_type === "module_page" && p.target_path)
    .sort((a, b) => a.target_path.localeCompare(b.target_path));
  const modulePaths = new Set(modulePages.map((p) => p.target_path));
  const claimedFileIds = new Set<string>();

  const moduleChildren: TreeNode[] = modulePages.map((mod) => {
    const files = pages
      .filter(
        (p) =>
          p.page_type === "file_page" &&
          p.target_path &&
          parentDirOf(p.target_path) === mod.target_path,
      )
      .sort((a, b) => a.target_path.localeCompare(b.target_path));
    for (const f of files) claimedFileIds.add(f.id);
    return {
      name: mod.title || mod.target_path.split("/").pop() || mod.target_path,
      path: mod.id,
      isDir: true,
      page: mod,
      children: files.map((f) => ({
        name: f.target_path.split("/").pop() || f.target_path,
        path: f.id,
        isDir: false,
        page: f,
        children: [],
      })),
    };
  });
  if (moduleChildren.length > 0) {
    sections.push({
      name: "Modules",
      path: SECTION_KEYS.modules,
      isDir: true,
      children: moduleChildren,
    });
  }

  // ---- Reference (everything not already surfaced above) ----
  const surfacedTypes = new Set([
    "module_page",
    "repo_overview",
    ...archTypes,
  ]);
  const refChildren: TreeNode[] = pages
    .filter(
      (p) =>
        !tourIds.has(p.id) &&
        !claimedFileIds.has(p.id) &&
        !surfacedTypes.has(p.page_type) &&
        // module-claimed dirs are not pages themselves; skip module dirs
        !modulePaths.has(p.target_path),
    )
    .sort((a, b) => (a.target_path || a.title).localeCompare(b.target_path || b.title))
    .map((p) => ({
      name: p.target_path ? p.target_path.split("/").pop() || p.target_path : p.title,
      path: p.id,
      isDir: false,
      page: p,
      children: [],
    }));
  if (refChildren.length > 0) {
    sections.push({
      name: "Reference",
      path: SECTION_KEYS.reference,
      isDir: true,
      children: refChildren,
    });
  }

  return sections;
}

// Ordinal shown next to each domain section so the spine reads as a numbered
// 1 → 4 reading order (Guided Tour → Architecture → Modules → Reference).
const SECTION_NUMBER: Record<string, number> = {
  [SECTION_KEYS.tour]: 1,
  [SECTION_KEYS.architecture]: 2,
  [SECTION_KEYS.modules]: 3,
  [SECTION_KEYS.reference]: 4,
};

function sectionIcon(path: string) {
  switch (path) {
    case SECTION_KEYS.tour:
      return Compass;
    case SECTION_KEYS.architecture:
      return Layers;
    case SECTION_KEYS.modules:
      return Network;
    case SECTION_KEYS.reference:
      return FileText;
    default:
      return BookOpen;
  }
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
): boolean {
  if (!page) return true; // directories always pass (will be pruned if empty)
  if (typeFilter !== "all" && page.page_type !== typeFilter) return false;
  if (freshnessFilter !== "all" && page.freshness_status !== freshnessFilter) return false;
  if (search) {
    const q = search.toLowerCase();
    return (
      page.title.toLowerCase().includes(q) ||
      page.target_path.toLowerCase().includes(q)
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
        ? matchesFilters(node.page, search, typeFilter, freshnessFilter)
        : false;
      if (filteredChildren.length > 0 || dirPageMatches) {
        result.push({ ...node, children: filteredChildren });
      }
    } else {
      if (matchesFilters(node.page, search, typeFilter, freshnessFilter)) {
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
}: {
  node: TreeNode;
  depth: number;
  selectedPageId: string | null;
  expandedDirs: Set<string>;
  toggleDir: (path: string) => void;
  onSelectPage: (page: DocPage) => void;
}) {
  const isExpanded = expandedDirs.has(node.path);
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
          className={cn(
            "flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-xs transition-colors hover:bg-[var(--color-bg-elevated)]",
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
          {node.path.startsWith("@section:") ? (
            (() => {
              const SectionIcon = sectionIcon(node.path);
              const num = SECTION_NUMBER[node.path];
              return (
                <>
                  {num != null && (
                    <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-tertiary)] tabular-nums">
                      {num}
                    </span>
                  )}
                  <SectionIcon className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)]" />
                </>
              );
            })()
          ) : node.path === ONBOARDING_DIR_KEY ? (
            <Compass className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)]" />
          ) : isExpanded ? (
            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)] opacity-70" />
          ) : (
            <Folder className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
          )}
          <span
            className={cn(
              "truncate font-medium",
              (node.path === ONBOARDING_DIR_KEY || node.path.startsWith("@section:")) &&
                "text-[var(--color-text-primary)]",
              node.path.startsWith("@section:") &&
                "text-[11px] uppercase tracking-wider",
            )}
          >
            {node.name}
          </span>
          {node.page && (
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
      className={cn(
        "flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-xs transition-colors hover:bg-[var(--color-bg-elevated)]",
        isSelected
          ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
          : "text-[var(--color-text-secondary)]",
      )}
      style={{ paddingLeft: `${depth * 16 + 8 + 16}px` }}
    >
      <PageIcon
        pageType={node.page?.page_type ?? "file_page"}
        className={cn(
          "h-3.5 w-3.5 shrink-0",
          isSelected ? "text-[var(--color-accent-primary)]" : "text-[var(--color-text-tertiary)]",
        )}
      />
      <span className="truncate">{node.name}</span>
      {node.page && (
        <FreshnessDot status={node.page.freshness_status as FreshnessStatus} />
      )}
    </button>
  );
}

function FreshnessDot({ status }: { status: FreshnessStatus }) {
  const color =
    status === "fresh"
      ? "bg-green-500"
      : status === "stale"
        ? "bg-yellow-500"
        : "bg-red-500";
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
    // Auto-expand first two levels, the Onboarding folder, and every domain
    // section so both views open in a useful state.
    const dirs = new Set<string>(DOMAIN_SECTION_KEYS);
    dirs.add(ONBOARDING_DIR_KEY);
    for (const page of pages) {
      const parts = page.target_path.split("/");
      if (parts.length > 1 && parts[0]) dirs.add(parts[0]);
    }
    return dirs;
  });
  const [showFilters, setShowFilters] = useState(true);

  const tree = useMemo(
    () => (viewMode === "domain" ? buildDomainTree(pages) : buildTree(pages)),
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

  // Stats
  const totalPages = pages.length;
  const freshCount = pages.filter((p) => p.freshness_status === "fresh").length;

  return (
    <div className={cn("flex flex-col h-full", className)}>
      {/* Search + filter bar */}
      <div className="p-3 space-y-2 border-b border-[var(--color-border-default)]">
        {/* View mode: semantic spine vs. raw filesystem */}
        <div className="flex items-center gap-1 rounded-md bg-[var(--color-bg-elevated)] p-0.5">
          {([
            { mode: "domain" as const, label: "By domain", Icon: Network },
            { mode: "folder" as const, label: "By folder", Icon: FolderTree },
          ]).map(({ mode, label, Icon }) => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1 text-[11px] font-medium transition-colors",
                viewMode === mode
                  ? "bg-[var(--color-bg-surface)] text-[var(--color-text-primary)] shadow-sm"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]",
              )}
            >
              <Icon className="h-3 w-3" />
              {label}
            </button>
          ))}
        </div>
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
          <button
            onClick={() => setShowFilters((s) => !s)}
            className={cn(
              "rounded-md p-1.5 transition-colors",
              showFilters
                ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                : "text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-secondary)]",
            )}
          >
            <Filter className="h-3.5 w-3.5" />
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
          </div>
        )}

        {/* Stats line */}
        <div className="flex items-center justify-between text-[10px] text-[var(--color-text-tertiary)]">
          <span>{totalPages} pages</span>
          <span>{freshCount} fresh · {totalPages - freshCount} need attention</span>
        </div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto p-1.5">
        {filteredTree.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-xs text-[var(--color-text-tertiary)]">
            <p>No matching pages</p>
          </div>
        ) : (
          <div className="space-y-0.5">
            {filteredTree.map((node) => (
              <TreeItem
                key={node.path}
                node={node}
                depth={0}
                selectedPageId={selectedPageId}
                expandedDirs={expandedDirs}
                toggleDir={toggleDir}
                onSelectPage={onSelectPage}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
