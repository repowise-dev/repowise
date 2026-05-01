"use client";

import { useState, useMemo } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  FolderOpen,
  Folder,
  Globe,
  LayoutGrid,
  Sparkles,
  Search,
  Filter,
} from "lucide-react";
import { cn } from "../lib/cn";
import { statusBadgeClasses, type FreshnessStatus } from "../lib/confidence";
import type { DocPage } from "@repowise/types/docs";

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

const PAGE_TYPE_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  repo_overview: Globe,
  architecture_diagram: LayoutGrid,
  module_page: FolderOpen,
  symbol_spotlight: Sparkles,
};

function PageIcon({ pageType, className }: { pageType: string; className?: string }) {
  const Icon = PAGE_TYPE_ICON[pageType] ?? FileText;
  return <Icon {...(className ? { className } : {})} />;
}

// ---------------------------------------------------------------------------
// Build tree from flat page list
// ---------------------------------------------------------------------------

function buildTree(pages: DocPage[]): TreeNode[] {
  const root: TreeNode[] = [];

  // Separate special pages (overview, architecture) from path-based pages
  const specialPages: DocPage[] = [];
  const pathPages: DocPage[] = [];

  for (const page of pages) {
    if (page.page_type === "repo_overview" || page.page_type === "architecture_diagram") {
      specialPages.push(page);
    } else {
      pathPages.push(page);
    }
  }

  // Add special pages at top level
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
  return root;
}

// ---------------------------------------------------------------------------
// Filter helpers
// ---------------------------------------------------------------------------

type TypeFilter = "all" | "file_page" | "module_page" | "symbol_spotlight" | "repo_overview";
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
          {isExpanded ? (
            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)] opacity-70" />
          ) : (
            <Folder className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
          )}
          <span className="truncate font-medium">{node.name}</span>
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

export function DocsTree({ pages, selectedPageId, onSelectPage, className }: DocsTreeProps) {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [freshnessFilter, setFreshnessFilter] = useState<FreshnessFilter>("all");
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(() => {
    // Auto-expand first two levels
    const dirs = new Set<string>();
    for (const page of pages) {
      const parts = page.target_path.split("/");
      if (parts.length > 1 && parts[0]) dirs.add(parts[0]);
    }
    return dirs;
  });
  const [showFilters, setShowFilters] = useState(false);

  const tree = useMemo(() => buildTree(pages), [pages]);
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
              {(["all", "file_page", "module_page", "symbol_spotlight", "repo_overview"] as TypeFilter[]).map((t) => (
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
                  {t === "all" ? "All" : t.replace(/_/g, " ").replace("page", "").trim() || "file"}
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
