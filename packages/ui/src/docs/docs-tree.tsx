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
  isStubPage,
  type OnboardingSlot,
} from "../lib/page-types";
import { RAW_GRAPH_ID, displayLabel, treeLabel } from "./page-labels";
import { groupPagesByLayer, readLayerOrder } from "../lib/layers";
import { cn } from "../lib/cn";
import { statusBadgeClasses, type FreshnessStatus } from "../lib/confidence";
import type { DocPageSummary } from "@repowise-dev/types/docs";

// Synthetic path used as the Onboarding folder's tree key. Distinct from any
// real target_path (which never starts with "@") so directory lookups don't
// collide with module paths.
const ONBOARDING_DIR_KEY = "@onboarding";
// Same idea for a layer's grouping row. A layer has no page behind it, so the
// row needs a key of its own and it must not look like a page id.
const LAYER_DIR_PREFIX = "@layer:";
const layerDirKey = (layerId: string) => `${LAYER_DIR_PREFIX}${layerId}`;
// And for the row holding the modules no layer claimed. Deliberately not a
// layerDirKey: this row is not a layer, and the key is what keeps it out of
// the default-expanded set (which only ever gets stamped layers' keys).
const UNLAYERED_MODULES_KEY = "@group:unlayered-modules";
// Tree expansion survives reloads (per-browser, not per-repo — paths rarely
// collide across repos and the fallback is just the default expansion).
//
// Versioned because the saved set is unioned with the defaults rather than
// replacing them: a reader whose browser recorded the old every-rung expansion
// would carry it forward and never see the outline the defaults now describe.
const EXPANDED_DIRS_KEY = "repowise:docs-tree-expanded:v2";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TreeNode {
  name: string;
  path: string;
  isDir: boolean;
  page?: DocPageSummary;
  children: TreeNode[];
  /** Dotted outline number from the stored tree ("2.4.1"), when the page has one. */
  section?: string;
}

interface DocsTreeProps {
  pages: DocPageSummary[];
  selectedPageId: string | null;
  onSelectPage: (page: DocPageSummary) => void;
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

function buildOnboardingFolder(pages: DocPageSummary[]): TreeNode | null {
  // Bucket every page by its onboarding slot. Both promoted pages
  // (repo_overview / architecture_diagram, tagged via metadata) and dedicated
  // `page_type === "onboarding"` pages flow into the same bucket.
  const bySlot = new Map<OnboardingSlot, DocPageSummary>();
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

function buildTree(allPages: DocPageSummary[]): TreeNode[] {
  // A tombstoned page documents a file that no longer exists. The folder view
  // mirrors the filesystem, so it drops them too, matching the domain view
  // (buildStoredTree) rather than listing pages for files that are gone.
  const pages = allPages.filter((p) => p.freshness_status !== "tombstone");
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
  const specialPages: DocPageSummary[] = [];
  const pathPages: DocPageSummary[] = [];

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

const isSpinePage = (page: DocPageSummary) => SPINE_TYPES.has(page.page_type);

// The whole concept spine goes without icons.
//
// This started as a rule for the middle rung only: modules and cycles nested
// under a layer all carry the same folder glyph, so a run of them reads as a
// column of identical icons that says nothing the indentation does not already
// say. The same argument holds one rung up. Every onboarding chapter draws the
// same compass and every layer the same stack, so the top of the tree was nine
// rows of near-identical glyphs down the left edge, which is a texture rather
// than information.
//
// Without them the outline reads by its shape — indentation for depth, weight
// for rank — which is how a table of contents has always worked. The
// deterministic file tree keeps its file/folder glyphs, where the icon does
// distinguish one row from the next.
const CONCEPT_CONTENT_TYPES = new Set([
  "module_page",
  "scc_page",
  "onboarding",
  "layer_page",
  "repo_overview",
]);

const hidesTreeIcon = (page?: DocPageSummary): boolean =>
  page ? CONCEPT_CONTENT_TYPES.has(page.page_type) : false;

// The same rule, for the outline rows that have no page behind them: a layer,
// the no-layer bucket, a by-type bucket. They were the one place a folder glyph
// still survived on the spine, and a column of them down the top rung is the
// texture the rule above exists to remove. The file corpus keeps its folder —
// there the glyph is what says "this row is not part of the outline".
const isOutlineGroupRow = (path: string): boolean =>
  path.startsWith(LAYER_DIR_PREFIX) ||
  path.startsWith(TYPE_GROUP_PREFIX) ||
  path === UNLAYERED_MODULES_KEY;

// The single bottom folder holding every deterministic page. Namespaced like
// the other synthetic keys so it can never collide with a real page id.
const AUTO_ROOT_KEY = "@group:auto-documented";

function compareSiblings(a: DocPageSummary, b: DocPageSummary): number {
  return (
    (a.display_order ?? 0) - (b.display_order ?? 0) ||
    (a.target_path || a.title).localeCompare(b.target_path || b.title)
  );
}

/**
 * Say out loud when the layer grouping and the repository disagree.
 *
 * A tree with no layer rows has two very different causes: a repository that
 * genuinely has no layers, and a page listing that stopped carrying the stamp
 * the grouping reads. They render identically, so the difference has to be
 * reported rather than looked at. The overview naming a spine while not one
 * page carries a stamp is the signal that it is the second one.
 *
 * The same goes one module at a time: a single module the layers do not claim
 * is invisible among dozens that are, so it is named here rather than left to
 * be spotted in the tree.
 */
function reportLayerGrouping(
  grouping: ReturnType<typeof groupPagesByLayer>,
  spine: readonly string[],
  unlayeredModules: readonly DocPageSummary[],
): void {
  if (spine.length > 0 && unlayeredModules.length > 0) {
    console.warn(
      `[docs-tree] ${unlayeredModules.length} module pages carry no layer stamp, so they are ` +
        "listed in a group of their own rather than inside a layer: " +
        unlayeredModules.map((p) => p.target_path || p.title).join(", "),
    );
  }
  if (spine.length > 0 && grouping.stamped === 0) {
    console.warn(
      `[docs-tree] the repository overview names ${spine.length} layers, but not one page ` +
        "carries a layer stamp, so the outline is flat. Check that the page listing still " +
        "serves layer_id.",
    );
  }
  if (spine.length === 0 && grouping.stamped > 0) {
    console.warn(
      `[docs-tree] ${grouping.stamped} pages name a layer, but the repository overview ` +
        "records no layer order. Grouping by name, which says nothing about which layer " +
        "depends on which.",
    );
  }
  if (spine.length > 0 && grouping.offSpine.length > 0) {
    console.warn(
      `[docs-tree] ${grouping.offSpine.length} layers are stamped on pages but absent from ` +
        `the overview's spine, so they sort last: ${grouping.offSpine.join(", ")}`,
    );
  }
}

function buildStoredTree(pages: DocPageSummary[]): TreeNode[] {
  // A tombstoned page documents a file that no longer exists. It keeps its row
  // and its content, but the tree deliberately has no place for it, so it must
  // be excluded here rather than treated as an unplaced page.
  const visible = pages.filter((p) => p.freshness_status !== "tombstone");

  // Two distinct surfaces. The spine is walked into the concept outline; every
  // deterministic page is set aside for the single bottom folder, so a file
  // page never appears in the outline itself.
  const spinePages = visible.filter(isSpinePage);
  const deterministicPages = visible.filter((p) => !isSpinePage(p));

  // Every deterministic page in one collapsed folder, held apart from the
  // concept outline so the distinction is obvious. Its interior reuses the
  // filesystem builder, so the files stay navigable by directory.
  const referenceFolder: TreeNode | null =
    deterministicPages.length > 0
      ? {
          name: `Auto-documented files (${deterministicPages.length})`,
          path: AUTO_ROOT_KEY,
          isDir: true,
          children: buildTree(deterministicPages),
        }
      : null;

  const byId = new Map(spinePages.map((p) => [p.id, p]));
  const childrenOf = new Map<string, DocPageSummary[]>();
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
  function toNode(page: DocPageSummary, parent: DocPageSummary | undefined): TreeNode {
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
    // The layers are the top rung of the outline, and they are grouping rows
    // rather than pages: the layer a module belongs to is stamped on the module
    // itself, so the grouping holds whether or not the wiki describes the layer
    // anywhere. On a wiki that does parent modules onto a layer page, no child
    // of the root carries a stamp and this is a no-op.
    const children = (childrenOf.get(root.id) ?? []).slice().sort(compareSiblings);
    const spine = readLayerOrder(root);
    const grouping = groupPagesByLayer(children, spine);
    // A module whose dominant layer came out empty gets no stamp, and left on
    // the top rung it is drawn exactly like a layer row — one module wearing
    // the weight of a whole architectural layer. Set those aside for a plainly
    // named row below the layers. Only when a spine exists: with no layers to
    // be missing from, there is nothing to say and nothing to move.
    const unlayeredModules =
      spine.length > 0
        ? grouping.ungrouped.filter((p) => p.page_type === "module_page")
        : [];
    reportLayerGrouping(grouping, spine, unlayeredModules);
    // The rest keep their place ahead of the layers: onboarding chapters and
    // the architecture diagram sort before any module, and that is the order a
    // reader should meet them in.
    const unlayered = new Set(unlayeredModules.map((p) => p.id));
    top.push(
      ...grouping.ungrouped
        .filter((child) => !unlayered.has(child.id))
        .map((child) => toNode(child, root)),
    );
    for (const group of grouping.groups) {
      top.push({
        name: group.label,
        path: layerDirKey(group.id),
        isDir: true,
        children: group.pages.map((child) => toNode(child, root)),
      });
    }
    // After the real layers, and named for what it is rather than as if it
    // were one more of them.
    if (unlayeredModules.length > 0) {
      top.push({
        name: `Modules with no layer (${unlayeredModules.length})`,
        path: UNLAYERED_MODULES_KEY,
        isDir: true,
        children: unlayeredModules.map((child) => toNode(child, root)),
      });
    }
  }

  // Concept pages the walk never reached. Grouped by type rather than dropped:
  // an unplaced page is still a page. On a store whose tree has not been built
  // yet this grouping IS the outline, a fair rendering of a wiki that genuinely
  // has no recorded hierarchy.
  const strayByType = new Map<string, DocPageSummary[]>();
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

  // The file corpus closes the tree, below every group above it and collapsed.
  // It is the largest thing in the wiki by a wide margin, and a row whose count
  // runs to four digits sitting mid-outline reads as the point of the sidebar
  // rather than as the reference it is. One row at the bottom keeps it in reach
  // without letting it set the shape of what a reader meets first.
  if (referenceFolder) top.push(referenceFolder);

  return top;
}

// ---------------------------------------------------------------------------
// Filter helpers
// ---------------------------------------------------------------------------

type TypeFilter = "all" | typeof ALL_PAGE_TYPES[number];
type FreshnessFilter = "all" | "fresh" | "stale" | "outdated";

function matchesFilters(
  page: DocPageSummary | undefined,
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

/**
 * Keep a top-level row's stored number only while it still reads as a sequence.
 *
 * The stored numbers are assigned across the whole outline, but only some of
 * the numbered pages reach the top rung. The orientation chapters land there
 * carrying 1-8; a layer's grouping row is not a page and carries no number at
 * all; and a module no layer claimed keeps its global number, so it arrives at
 * the top rung stamped "41" and sits next to the "1". Rendered as stored, the
 * column of numbers skips from 8 to 41 and tells a reader nothing.
 *
 * So a row keeps its number only when that number continues the run from 1;
 * every other top-level row renders none. The visible numbering is then
 * contiguous or absent, never a jump. Deeper rows are unaffected — they never
 * render a number in the first place.
 *
 * The rule is about the run rather than about which kind of page a row is: if
 * the outline is later numbered end to end, the numbers come back on their own.
 */
function keepContiguousSections(nodes: TreeNode[]): TreeNode[] {
  let expected = 1;
  return nodes.map((node) => {
    if (node.section === String(expected)) {
      expected += 1;
      return node;
    }
    if (!node.section) return node;
    const unnumbered: TreeNode = { ...node };
    delete unnumbered.section;
    return unnumbered;
  });
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
  onSelectPage: (page: DocPageSummary) => void;
  /** Open every dir while a search is active so matches are never hidden. */
  forceExpand?: boolean;
  /** Per-row freshness dots are opt-in — off by default to keep rows quiet. */
  showFreshness?: boolean;
}) {
  const isExpanded = forceExpand || expandedDirs.has(node.path);
  const isSelected = node.page && node.page.id === selectedPageId;
  const hasChildren = node.children.length > 0;

  if (node.isDir) {
    const row = (
      <button
        onClick={() => {
          toggleDir(node.path);
          if (node.page) onSelectPage(node.page);
        }}
        {...(node.page && node.page.title !== node.name ? { title: node.page.title } : {})}
        className={cn(
          "flex w-full min-w-0 flex-1 items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-bg-elevated)]",
          // Top-level sections (layers, the Onboarding folder, the bottom
          // Auto-documented folder) get air above them so each group reads as
          // a distinct block rather than one long list.
          depth === 0 && "mt-2 first:mt-0",
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
        ) : hidesTreeIcon(node.page) ? null : node.page ? (
          // A layer keeps its section icon as the anchor for its group; a
          // concept content dir (a module with children) drops its folder
          // glyph so the outline is not a column of identical icons. The
          // chevron already marks it as expandable.
          <PageIcon
            pageType={node.page.page_type}
            className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)]"
          />
        ) : isOutlineGroupRow(node.path) ? null : isExpanded ? (
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)] opacity-70" />
        ) : (
          <Folder className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
        )}
        <span
          className={cn(
            // Wraps rather than truncates. A section title cut to
            // "Documentation Generation Engi…" names nothing, and the tree
            // has the vertical room — it is a list of a few dozen concept
            // rows, not a viewport-bound table.
            "min-w-0 text-left font-medium [overflow-wrap:anywhere]",
            // A top-level section is the parent of everything indented under
            // it, so it carries the strongest weight in the tree.
            (node.path === ONBOARDING_DIR_KEY || depth === 0) &&
              "font-semibold text-[var(--color-text-primary)]",
          )}
        >
          {node.name}
        </span>
        <RowMarkers page={node.page} showFreshness={showFreshness} />
      </button>
    );

    return (
      <div>
        {row}

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
      {!hidesTreeIcon(node.page) && (
        <PageIcon
          pageType={node.page?.page_type ?? "file_page"}
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            isSelected
              ? "text-[var(--color-accent-primary)]"
              : "text-[var(--color-text-tertiary)]",
          )}
        />
      )}
      <span className="min-w-0 text-left [overflow-wrap:anywhere]">{node.name}</span>
      <RowMarkers page={node.page} showFreshness={showFreshness} />
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
  return <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", color)} />;
}

/**
 * A page a model is expected to write that has not been written yet.
 *
 * Whether a page carries prose was previously only discoverable by opening it
 * and reading the rail, so "how far does the prose go" took one click per page.
 * Marking it in the tree answers that while browsing.
 *
 * Hollow, and only on stubs — same reasoning as FreshnessDot above. A marker
 * on every row says nothing; this one means there is something left to do.
 * Structural pages (files, symbols, contracts) are templates by design and are
 * never marked.
 */
function StubDot({ page }: { page: DocPageSummary }) {
  if (!isStubPage(page)) return null;
  return (
    <span
      title="Built from the index. A model has not written this page yet."
      className="h-1.5 w-1.5 shrink-0 rounded-full ring-1 ring-[var(--color-text-tertiary)]"
    />
  );
}

/** Trailing status markers, right-aligned as one group so the two dots do not
 *  fight over `ml-auto`. */
function RowMarkers({
  page,
  showFreshness,
}: {
  page: DocPageSummary | undefined;
  showFreshness: boolean;
}) {
  if (!page) return null;
  return (
    <span className="ml-auto flex shrink-0 items-center gap-1 pl-1">
      <StubDot page={page} />
      {showFreshness && (
        <FreshnessDot status={page.freshness_status as FreshnessStatus} />
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main DocsTree component
// ---------------------------------------------------------------------------

/** Keys of every dir on the way down to the row holding `pageId`, root first.
 *  Empty when the page is not in this tree, or is on the top rung already. */
function ancestorDirs(nodes: TreeNode[], pageId: string): string[] {
  const trail: string[] = [];
  function walk(list: TreeNode[]): boolean {
    for (const node of list) {
      if (node.page?.id === pageId) return true;
      if (!node.children.length) continue;
      trail.push(node.path);
      if (walk(node.children)) return true;
      trail.pop();
    }
    return false;
  }
  return walk(nodes) ? [...trail] : [];
}

type ViewMode = "domain" | "folder";

export function DocsTree({
  pages,
  selectedPageId,
  onSelectPage,
  className,
}: DocsTreeProps) {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [freshnessFilter, setFreshnessFilter] = useState<FreshnessFilter>("all");
  // Default to the semantic "By domain" spine — overview/architecture/modules
  // first, filesystem second. The folder view is a toggle for power users.
  const [viewMode, setViewMode] = useState<ViewMode>("domain");
  // Built before the expansion state because the defaults are read off the
  // tree's own top rung rather than guessed from the flat page list.
  const tree = useMemo(
    () => (viewMode === "domain" ? buildStoredTree(pages) : buildTree(pages)),
    [pages, viewMode],
  );
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(() => {
    // The Onboarding folder, the by-type buckets that hold pages the stored
    // tree does not reach (on a store whose tree has not been built yet those
    // buckets are the whole tree, so leaving them shut would show almost
    // nothing), and the top rung below. Then ADD any previously expanded dirs
    // from localStorage. Union (not replace) — the key is shared across repos,
    // so a stale saved set must never collapse another repo's default-open rows.
    const dirs = new Set<string>(STRAY_GROUP_KEYS);
    dirs.add(ONBOARDING_DIR_KEY);
    // One rung, and only one. Every group on the top rung opens, so the first
    // screen is a table of contents — the layers named, with what each holds
    // listed under it — rather than a column of shut rows that says nothing
    // until it is clicked.
    //
    // Nothing below that rung opens with it. A chapter that parents its own
    // sub-chapters used to expand too, wherever it sat, and the outline came
    // out four rungs deep on load: the indentation stopped carrying rank and
    // the tree read as a filesystem. Shut, a chapter costs one click and its
    // parent's list stays readable. This is also what the old layers-shut
    // default was protecting against — roughly ninety module rows on the first
    // screen — and the rung limit protects against it without hiding the
    // outline to do so.
    //
    // Two exclusions, both rows that are a bucket rather than a section: the
    // file corpus, which runs to thousands of rows, and the no-layer leftovers.
    for (const node of tree) {
      if (!node.isDir) continue;
      if (node.path === AUTO_ROOT_KEY || node.path === UNLAYERED_MODULES_KEY) continue;
      dirs.add(node.path);
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
  // Reveal whatever is being read. Only the top rung opens by default, so a
  // page reached by any route other than clicking its own row — a deep link, a
  // "related pages" link, the command palette — would otherwise be selected
  // inside a shut chapter with nothing in the tree to show where it sits.
  useEffect(() => {
    if (!selectedPageId) return;
    const ancestors = ancestorDirs(tree, selectedPageId);
    if (!ancestors.length) return;
    setExpandedDirs((prev) => {
      if (ancestors.every((p) => prev.has(p))) return prev;
      const next = new Set(prev);
      for (const p of ancestors) next.add(p);
      return next;
    });
  }, [selectedPageId, tree]);
  // Filters are a power-user affordance — start hidden so the panel opens
  // calm; the funnel button shows a count when any filter is active.
  const [showFilters, setShowFilters] = useState(false);
  // Per-row freshness dots are opt-in noise — off by default. Turning this on
  // (or filtering by status) is how a reader audits staleness across the tree.
  const [showFreshness, setShowFreshness] = useState(false);

  // Numbering is decided on what actually renders, after filtering, so the
  // visible run stays contiguous even when a filter hides a numbered row.
  const filteredTree = useMemo(
    () => keepContiguousSections(filterTree(tree, search, typeFilter, freshnessFilter)),
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

  // Count what the tree actually shows. Both views hide tombstones, so a raw
  // pages.length overstates the wiki by the number of deleted-file rows.
  const totalPages = useMemo(
    () => pages.filter((p) => p.freshness_status !== "tombstone").length,
    [pages],
  );
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
