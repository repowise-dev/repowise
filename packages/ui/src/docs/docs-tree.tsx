"use client";

import { useEffect, useState, useMemo } from "react";
import { useDebounce } from "../hooks/use-debounce";
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
import { cn } from "../lib/cn";
import { formatNumber } from "../lib/format";
import { statusBadgeClasses, type FreshnessStatus } from "../lib/confidence";
import type { DocPageSummary } from "@repowise-dev/types/docs";

// Synthetic path used as the Onboarding folder's tree key. Distinct from any
// real target_path (which never starts with "@") so directory lookups don't
// collide with module paths.
const ONBOARDING_DIR_KEY = "@onboarding";
// The end-to-end walkthroughs get a row of their own after the sections: a
// repo can carry many, and listed at the top they outnumber the few pages a
// newcomer should open first.
const WALKTHROUGHS_KEY = "@group:walkthroughs";
// Small top-level packages the stored tree numbers as one section.
const SUPPORTING_KEY = "@group:supporting-packages";
// The bottom row holding what the project has been doing lately.
const PROJECT_ACTIVITY_KEY = "@group:project-activity";
// The reference folder's sub-folders, one per kind of deterministic page.
const REF_CONTRACTS_KEY = "@group:reference:api";
const REF_FILES_KEY = "@group:reference:files";
const REF_INFRA_KEY = "@group:reference:infra";
const REF_CYCLES_KEY = "@group:reference:cycles";
// Tree expansion survives reloads (per-browser, not per-repo — paths rarely
// collide across repos and the fallback is just the default expansion).
//
// Versioned because the saved set is unioned with the defaults rather than
// replacing them: a reader whose browser recorded the old every-rung expansion
// would carry it forward and never see the outline the defaults now describe.
const EXPANDED_DIRS_KEY = "repowise:docs-tree-expanded:v2";

// A search opens every directory so its matches are never hidden, which on a
// large index meant thousands of rows mounted per keystroke. The query waits
// for typing to settle, and the rows render in pages of this size.
const SEARCH_DEBOUNCE_MS = 150;
const SEARCH_ROW_PAGE = 200;

// ---------------------------------------------------------------------------
// Copy
// ---------------------------------------------------------------------------

export interface DocsTreeCopy {
  searchPlaceholder: string;
  folderView: string;
  domainView: string;
  switchToFolderView: string;
  switchToDomainView: string;
  toggleFilters: string;
  typeLabel: string;
  statusLabel: string;
  all: string;
  status: Record<Exclude<StatusFilter, "all">, string>;
  showFreshnessDots: string;
  pageCount: (count: number) => string;
  /** One clickable segment of the summary beside the page count. */
  summary: Record<SummaryStatus, (count: number) => string>;
  noMatches: string;
  showMore: (next: number, remaining: number) => string;
  stubTitle: string;
  onboarding: string;
  walkthroughs: (count: number) => string;
  supportingPackages: (count: number) => string;
  codeReference: string;
  codeReferenceMeta: (counts: { files: number; symbols: number; other: number }) => string;
  referenceApi: string;
  referenceFiles: string;
  referenceInfrastructure: string;
  referenceCycles: string;
  projectActivity: string;
}


export const DEFAULT_DOCS_TREE_COPY: DocsTreeCopy = {
  searchPlaceholder: "Search docs...",
  folderView: "Folder view",
  domainView: "Domain view",
  switchToFolderView: "Switch to folder view",
  switchToDomainView: "Switch to domain view",
  toggleFilters: "Toggle filters",
  typeLabel: "Type",
  statusLabel: "Status",
  all: "All",
  status: { fresh: "Fresh", stale: "Stale", outdated: "Outdated", unwritten: "Not written" },
  showFreshnessDots: "Show freshness dots on every row",
  pageCount: (n) => `${formatNumber(n)} ${n === 1 ? "page" : "pages"}`,
  summary: {
    stale: (n) => `${formatNumber(n)} stale`,
    outdated: (n) => `${formatNumber(n)} outdated`,
    unwritten: (n) => `${formatNumber(n)} not written`,
  },
  noMatches: "No matching pages",
  showMore: (next, remaining) => `Show ${formatNumber(next)} more (${formatNumber(remaining)} left)`,
  stubTitle: "Built from the index. A model has not written this page yet.",
  onboarding: "Onboarding",
  walkthroughs: (n) => `Walkthroughs (${formatNumber(n)})`,
  supportingPackages: (n) => `Supporting packages (${formatNumber(n)})`,
  codeReference: "Reference",
  codeReferenceMeta: ({ files, symbols, other }) =>
    [
      `${formatNumber(files)} ${files === 1 ? "file" : "files"}`,
      `${formatNumber(symbols)} ${symbols === 1 ? "symbol" : "symbols"}`,
      ...(other > 0 ? [`${formatNumber(other)} other`] : []),
    ].join(" · "),
  referenceApi: "API",
  referenceFiles: "Files",
  referenceInfrastructure: "Infrastructure",
  referenceCycles: "Dependency cycles",
  projectActivity: "Project activity",
};

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
  /** Quiet trailing detail on a group row, such as what a folder holds. */
  meta?: string;
}

interface DocsTreeProps {
  pages: DocPageSummary[];
  selectedPageId: string | null;
  onSelectPage: (page: DocPageSummary) => void;
  className?: string;
  /** Overrides for the tree's wording, for a host translating it. */
  copy?: Partial<DocsTreeCopy> | undefined;
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

function buildOnboardingFolder(pages: DocPageSummary[], label: string): TreeNode | null {
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
    name: label,
    path: ONBOARDING_DIR_KEY,
    isDir: true,
    children,
  };
}

/** The file page a symbol spotlight hangs off: its stored parent, or the page
 *  for the path before `::` when the row predates the stored tree. */
function symbolFileId(page: DocPageSummary): string | null {
  if (page.page_type !== "symbol_spotlight") return null;
  if (page.parent_page_id) return page.parent_page_id;
  const sep = page.target_path.indexOf("::");
  return sep === -1 ? null : `file_page:${page.target_path.slice(0, sep)}`;
}

/** A row that holds other rows and is not itself a page about one file: a
 *  directory, or a module. A file row holding its symbols is still a file. */
function isFolderRow(node: TreeNode): boolean {
  return node.isDir && (!node.page || node.page.page_type === "module_page");
}

/**
 * Hang each symbol spotlight under the row of the file it was found in, so a
 * file's symbols are one click under it rather than flat siblings beside it.
 * Returns the symbols whose file has no row here; they keep their place in
 * the path-based tree.
 */
function nestSymbolsUnderFiles(
  symbols: DocPageSummary[],
  fileRows: Map<string, TreeNode>,
): DocPageSummary[] {
  const orphans: DocPageSummary[] = [];
  for (const page of symbols) {
    const fileId = symbolFileId(page);
    const fileRow = fileId ? fileRows.get(fileId) : undefined;
    if (!fileRow) {
      orphans.push(page);
      continue;
    }
    fileRow.isDir = true;
    fileRow.children.push({
      name: treeLabel(page, fileRow.page),
      path: page.id,
      isDir: false,
      page,
      children: [],
    });
  }
  return orphans;
}

function buildTree(allPages: DocPageSummary[], copy: DocsTreeCopy): TreeNode[] {
  // A tombstoned page documents a file that no longer exists. The folder view
  // mirrors the filesystem, so it drops them too, matching the domain view
  // (buildStoredTree) rather than listing pages for files that are gone.
  const pages = allPages.filter((p) => p.freshness_status !== "tombstone");
  const root: TreeNode[] = [];

  // ---- Onboarding folder (always at top when any slot is filled) ----
  const onboardingFolder = buildOnboardingFolder(pages, copy.onboarding);
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

  // Symbols are placed after their files exist, under the file's own row.
  const symbolPages = pathPages.filter((p) => p.page_type === "symbol_spotlight");
  const fileRows = new Map<string, TreeNode>();

  const placePage = (page: DocPageSummary) => {
    const targetPath = page.target_path;
    if (!targetPath) return;

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
      fileRows.set(page.id, fileNode);

      if (parts.length > 1) {
        const parentPath = parts.slice(0, -1).join("/");
        const parent = ensureDir(parentPath);
        parent.children.push(fileNode);
      } else {
        root.push(fileNode);
      }
    }
  };
  for (const page of pathPages) {
    if (page.page_type !== "symbol_spotlight") placePage(page);
  }
  for (const page of nestSymbolsUnderFiles(symbolPages, fileRows)) placePage(page);

  // Add top-level directories to root
  for (const [dirPath, node] of dirMap) {
    if (!dirPath.includes("/")) {
      root.push(node);
    }
  }

  // Sort children: folders first, then files, both alphabetically. A file
  // holding its symbols stays among the files.
  function sortChildren(nodes: TreeNode[]) {
    nodes.sort((a, b) => {
      const af = isFolderRow(a);
      const bf = isFolderRow(b);
      if (af !== bf) return af ? -1 : 1;
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
// Shape:
//
//   Overview                   the root, rendered first
//   1   Getting Started        the reading path, in canonical order
//   3   sections               module pages
//   3.1   their parts          anything deeper listed flat under a part
//   11  Supporting packages    small top-level packages, when there are many
//   12  Walkthroughs           the flow pages
//       Reference              API, Files (with symbols), Infrastructure, cycles
//       Project activity
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
  "flow_page",
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
  "flow_page",
]);

const hidesTreeIcon = (page?: DocPageSummary): boolean =>
  page ? CONCEPT_CONTENT_TYPES.has(page.page_type) : false;

// The same rule, for the outline rows that have no page behind them: a layer,
// the no-layer bucket, a by-type bucket. They were the one place a folder glyph
// still survived on the spine, and a column of them down the top rung is the
// texture the rule above exists to remove. The file corpus keeps its folder —
// there the glyph is what says "this row is not part of the outline".
const isOutlineGroupRow = (path: string): boolean =>
  path.startsWith(TYPE_GROUP_PREFIX) ||
  path === WALKTHROUGHS_KEY ||
  path === SUPPORTING_KEY ||
  path === PROJECT_ACTIVITY_KEY;

// The single bottom folder holding every deterministic page. Namespaced like
// the other synthetic keys so it can never collide with a real page id.
const AUTO_ROOT_KEY = "@group:auto-documented";
// Top-rung groups that open shut. Everything else on that rung opens.
const SHUT_BY_DEFAULT = new Set([
  AUTO_ROOT_KEY,
  WALKTHROUGHS_KEY,
  SUPPORTING_KEY,
  PROJECT_ACTIVITY_KEY,
]);

function compareSiblings(a: DocPageSummary, b: DocPageSummary): number {
  return (
    (a.display_order ?? 0) - (b.display_order ?? 0) ||
    (a.target_path || a.title).localeCompare(b.target_path || b.title)
  );
}

/** The deterministic pages, split by kind so the folder can say what it holds. */
function countReferencePages(pages: DocPageSummary[]) {
  let files = 0;
  let symbols = 0;
  for (const p of pages) {
    if (p.page_type === "file_page") files += 1;
    else if (p.page_type === "symbol_spotlight") symbols += 1;
  }
  return { files, symbols, other: pages.length - files - symbols };
}

const pagesOfType = (pages: DocPageSummary[], type: string): DocPageSummary[] =>
  pages.filter((p) => p.page_type === type).sort(compareSiblings);

/** Onboarding slots that describe recent activity rather than how the project
 *  works. They close the outline as "Project activity". Mirrors
 *  `PROJECT_ACTIVITY_SLOTS` in `core/generation/page_tree.py`. */
const PROJECT_ACTIVITY_SLOTS = new Set<string>(["active_landscape"]);

const isProjectActivity = (page: DocPageSummary): boolean => {
  // Stamped on pages written since the stamp existed; older ones by slot.
  if (page.metadata?.["nav_section"] === "project_activity") return true;
  if (page.page_type !== "onboarding") return false;
  const slot = getOnboardingSlot(page) ?? page.target_path.split("/").pop() ?? "";
  return PROJECT_ACTIVITY_SLOTS.has(slot);
};

// A section and two rungs below it. Anything deeper is listed, flat, under the
// second rung: still one click away, never a fourth level of indentation.
const MAX_OUTLINE_DEPTH = 3;

/** Every descendant of *page* in tree order, for the rung that flattens them. */
function descendantsOf(
  page: DocPageSummary,
  childrenOf: Map<string, DocPageSummary[]>,
  reached: Set<string>,
): DocPageSummary[] {
  const out: DocPageSummary[] = [];
  for (const child of [...(childrenOf.get(page.id) ?? [])].sort(compareSiblings)) {
    if (reached.has(child.id)) continue;
    reached.add(child.id);
    out.push(child, ...descendantsOf(child, childrenOf, reached));
  }
  return out;
}

/** The stored number when it is one of the outline's two levels ("4", "4.2").
 *  A deeper one comes from a store numbered before the cap and is not shown. */
const outlineSection = (page: DocPageSummary): string | undefined => {
  const section = page.section_number;
  return section && section.split(".").length <= 2 ? section : undefined;
};

/** The resource each contract describes, from its path: what follows the
 *  directories every contract shares, without the extension. */
function resourceLabels(pages: DocPageSummary[]): Map<string, string> {
  const dirs = pages.map((p) => p.target_path.split("/").slice(0, -1));
  let common = dirs[0] ?? [];
  for (const parts of dirs.slice(1)) {
    let i = 0;
    while (i < common.length && i < parts.length && common[i] === parts[i]) i++;
    common = common.slice(0, i);
  }
  // Keep one directory of context even when every contract sits in it.
  const cut = Math.max(0, Math.min(common.length, ...dirs.map((d) => d.length - 1)));
  return new Map(
    pages.map((p) => [
      p.id,
      p.target_path.split("/").slice(cut).join("/").replace(/\.[^./]+$/, "") || p.target_path,
    ]),
  );
}

/** Reference pages of one kind as a flat, sorted folder. */
function referenceGroup(
  key: string,
  name: string,
  pages: DocPageSummary[],
  label: (page: DocPageSummary) => string,
): TreeNode | null {
  if (pages.length === 0) return null;
  const children: TreeNode[] = pages
    .map((page) => ({ name: label(page), path: page.id, isDir: false, page, children: [] }))
    .sort((a, b) => a.name.localeCompare(b.name));
  return { name, path: key, isDir: true, children, meta: formatNumber(pages.length) };
}

// "Circular Dependency: " and its translations: the folder already says it.
const CYCLE_PREFIX = /^[^:]{1,40}:\s*/;

/** Every deterministic page in one collapsed folder, held apart from the
 *  outline, one sub-folder per kind. Files keep their directory tree, each file
 *  holding its own symbols, so a symbol is reached through its file or by
 *  search rather than as one of thousands of rows of its own. */
function buildReferenceFolder(pages: DocPageSummary[], copy: DocsTreeCopy): TreeNode | null {
  if (pages.length === 0) return null;
  const of = (type: string) => pages.filter((p) => p.page_type === type);
  const contracts = of("api_contract");
  const resources = resourceLabels(contracts);
  const grouped = new Set(["api_contract", "infra_page", "scc_page"]);
  const files = pages.filter((p) => !grouped.has(p.page_type));
  const children = [
    referenceGroup(REF_CONTRACTS_KEY, copy.referenceApi, contracts, (p) => resources.get(p.id) ?? p.target_path),
    files.length > 0
      ? {
          name: copy.referenceFiles,
          path: REF_FILES_KEY,
          isDir: true,
          children: buildTree(files, copy),
          meta: copy.codeReferenceMeta(countReferencePages(files)),
        }
      : null,
    referenceGroup(REF_INFRA_KEY, copy.referenceInfrastructure, of("infra_page"), (p) => p.target_path),
    referenceGroup(REF_CYCLES_KEY, copy.referenceCycles, of("scc_page"), (p) =>
      displayLabel(p).replace(CYCLE_PREFIX, ""),
    ),
  ].filter((node): node is TreeNode => node !== null);
  return { name: copy.codeReference, path: AUTO_ROOT_KEY, isDir: true, children };
}

/** A group row on the top rung, numbered as the section its pages sit in
 *  ("12" for pages numbered "12.1", "12.2"). `null` when it holds nothing. */
function groupRow(path: string, name: string, pages: DocPageSummary[]): TreeNode | null {
  if (pages.length === 0) return null;
  const first = pages[0]?.section_number ?? "";
  const section = first.includes(".") ? first.split(".")[0] : undefined;
  return { name, path, isDir: true, children: [], ...(section ? { section } : {}) };
}

function buildStoredTree(pages: DocPageSummary[], copy: DocsTreeCopy): TreeNode[] {
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
  function toNode(page: DocPageSummary, parent: DocPageSummary | undefined, depth: number): TreeNode {
    reached.add(page.id);
    const name = treeLabel(page, parent);
    // A part named as its section is the section's own page ("Repowise Core"
    // under Core, "Web Source" under Web): its row folds into the section's
    // and whatever it holds is listed in its place.
    const restates = (node: TreeNode) =>
      node.page?.page_type === "module_page" && node.name.toLowerCase() === name.toLowerCase();
    const kids: TreeNode[] =
      depth < MAX_OUTLINE_DEPTH - 1
        ? (childrenOf.get(page.id) ?? [])
            .filter((c) => !reached.has(c.id))
            .sort(compareSiblings)
            .map((c) => toNode(c, page, depth + 1))
            .flatMap((node) => (restates(node) ? node.children : [node]))
        : descendantsOf(page, childrenOf, reached)
            .map((c): TreeNode => {
              const section = outlineSection(c);
              return {
                name: treeLabel(c, page),
                path: c.id,
                isDir: false,
                page: c,
                children: [],
                ...(section ? { section } : {}),
              };
            })
            .filter((node) => !restates(node));
    const section = outlineSection(page);
    return {
      name,
      path: page.id,
      isDir: kids.length > 0,
      page,
      children: kids,
      ...(section ? { section } : {}),
    };
  }

  const top: TreeNode[] = [];
  // Walkthroughs and project activity are claimed before any row is built, so
  // a chapter that parents one never nests it and each is drawn once, in its
  // own group.
  const flows = pagesOfType(spinePages, "flow_page");
  const activity = spinePages.filter(isProjectActivity).sort(compareSiblings);
  for (const page of [...flows, ...activity]) reached.add(page.id);

  if (root) {
    reached.add(root.id);
    top.push({ name: treeLabel(root, undefined), path: root.id, isDir: false, page: root, children: [] });
    // The reading path, then the sections, in stored order: one rung, each
    // row carrying its number. Layers group nothing here.
    const rows = (childrenOf.get(root.id) ?? [])
      .filter((c) => !reached.has(c.id))
      .sort(compareSiblings);
    // A top-level module numbered inside a section ("13.2") is one of the
    // small packages the stored tree lists together after the sections.
    const supporting = rows.filter(
      (p) => p.page_type === "module_page" && (p.section_number ?? "").includes("."),
    );
    const grouped = new Set(supporting.map((p) => p.id));
    const sections = rows.filter((p) => !grouped.has(p.id));
    for (const child of sections) top.push(toNode(child, root, 1));
    const group = groupRow(SUPPORTING_KEY, copy.supportingPackages(supporting.length), supporting);
    if (group) {
      group.children = supporting.map((page) => toNode(page, root, 2));
      top.push(group);
    }
  } else {
    // No stored tree yet: the reading path still leads.
    const lead = [
      ...pagesOfType(spinePages, "repo_overview"),
      ...pagesOfType(spinePages, "onboarding").filter((p) => !reached.has(p.id)),
    ];
    for (const page of lead) top.push(toNode(page, undefined, 1));
  }

  // Walkthroughs follow the sections, numbered as one more section.
  const walkthroughs = groupRow(WALKTHROUGHS_KEY, copy.walkthroughs(flows.length), flows);
  if (walkthroughs) {
    walkthroughs.children = flows.map((page) => toNode(page, root ?? undefined, 2));
    top.push(walkthroughs);
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

  // The reference corpus, below the outline and collapsed. It is the largest
  // thing in the wiki by a wide margin, and a row whose count runs to four
  // digits mid-outline reads as the point of the sidebar rather than as the
  // reference it is.
  const referenceFolder = buildReferenceFolder(deterministicPages, copy);
  if (referenceFolder) top.push(referenceFolder);

  // What has been happening in the project, last: it describes the history,
  // not how the code works.
  if (activity.length > 0) {
    top.push({
      name: copy.projectActivity,
      path: PROJECT_ACTIVITY_KEY,
      isDir: true,
      children: activity.map((page) => toNode(page, root ?? undefined, 2)),
    });
  }

  return top;
}

// ---------------------------------------------------------------------------
// Filter helpers
// ---------------------------------------------------------------------------

type TypeFilter = "all" | typeof ALL_PAGE_TYPES[number];
/** A page's state: its stored freshness, or "unwritten" for a page a model is
 *  expected to write and has not (the tree's hollow stub dot). */
export type StatusFilter = "all" | "fresh" | "stale" | "outdated" | "unwritten";
const STATUS_FILTERS: StatusFilter[] = ["all", "fresh", "stale", "outdated", "unwritten"];
/** The states worth a line beside the page count, in the order they read. */
const SUMMARY_STATUSES = ["stale", "outdated", "unwritten"] as const;
type SummaryStatus = (typeof SUMMARY_STATUSES)[number];
type StatusCounts = Record<SummaryStatus, number>;

function matchesStatus(page: DocPageSummary, status: StatusFilter): boolean {
  if (status === "all") return true;
  if (status === "unwritten") return isStubPage(page);
  return page.freshness_status === status;
}

/** How many visible pages are in each state the summary names. */
function countPageStatuses(pages: DocPageSummary[]): StatusCounts {
  const counts: StatusCounts = { stale: 0, outdated: 0, unwritten: 0 };
  for (const page of pages) {
    if (page.freshness_status === "stale") counts.stale += 1;
    else if (page.freshness_status === "outdated") counts.outdated += 1;
    if (isStubPage(page)) counts.unwritten += 1;
  }
  return counts;
}

function matchesFilters(
  page: DocPageSummary | undefined,
  search: string,
  typeFilter: TypeFilter,
  freshnessFilter: StatusFilter,
  displayName?: string,
): boolean {
  if (!page) return true; // directories always pass (will be pruned if empty)
  if (typeFilter !== "all" && page.page_type !== typeFilter) return false;
  if (!matchesStatus(page, freshnessFilter)) return false;
  if (search) {
    // `search` arrives lower-cased once per query, not once per row.
    const q = search;
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
  freshnessFilter: StatusFilter,
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
 * The first `limit` rows of a fully expanded tree, in render order, and how
 * many rows the whole tree has. A search expands every directory, so this is
 * what bounds the rows it mounts; ancestors of a kept row are always kept.
 */
function capTreeRows(
  nodes: TreeNode[],
  limit: number,
): { nodes: TreeNode[]; total: number } {
  let budget = limit;
  let total = 0;
  function walk(list: TreeNode[]): TreeNode[] {
    const kept: TreeNode[] = [];
    for (const node of list) {
      total += 1;
      if (budget <= 0) {
        walk(node.children); // still counted, not kept
        continue;
      }
      budget -= 1;
      kept.push(node.children.length ? { ...node, children: walk(node.children) } : node);
    }
    return kept;
  }
  const kept = walk(nodes);
  return { nodes: kept, total };
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
  const keep = (node: TreeNode): TreeNode => {
    if (node.section === String(expected)) {
      expected += 1;
      return node;
    }
    if (!node.section) return node;
    const unnumbered: TreeNode = { ...node };
    delete unnumbered.section;
    return unnumbered;
  };
  return nodes.map(keep);
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
  stubTitle,
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
  /** Tooltip on the hollow dot marking a page with no prose yet. */
  stubTitle: string;
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
            ? "bg-[var(--color-selection-wash)] text-[var(--color-text-primary)]"
            : "text-[var(--color-text-secondary)]",
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        <DirChevron hasChildren={hasChildren} isExpanded={isExpanded} />
        <SectionNumber section={node.section} />
        <DirIcon node={node} isExpanded={isExpanded} />
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
          {/* Under the name rather than beside it, so a long count never
              squeezes the label it describes. */}
          {node.meta && (
            <span className="block font-mono text-[10px] font-normal tabular-nums text-[var(--color-text-tertiary)]">
              {node.meta}
            </span>
          )}
        </span>
        <RowMarkers page={node.page} showFreshness={showFreshness} stubTitle={stubTitle} />
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
                stubTitle={stubTitle}
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
          ? "bg-[var(--color-selection-wash)] text-[var(--color-text-primary)]"
          : "text-[var(--color-text-secondary)]",
      )}
      style={{ paddingLeft: `${depth * 16 + 8 + 16}px` }}
    >
      <SectionNumber section={node.section} />
      {!hidesTreeIcon(node.page) && (
        <PageIcon
          pageType={node.page?.page_type ?? "file_page"}
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            isSelected
              ? "text-[var(--color-text-primary)]"
              : "text-[var(--color-text-tertiary)]",
          )}
        />
      )}
      <span className="min-w-0 text-left [overflow-wrap:anywhere]">{node.name}</span>
      <RowMarkers page={node.page} showFreshness={showFreshness} stubTitle={stubTitle} />
    </button>
  );
}

function DirChevron({ hasChildren, isExpanded }: { hasChildren: boolean; isExpanded: boolean }) {
  if (!hasChildren) return <span className="w-3 shrink-0" />;
  const Icon = isExpanded ? ChevronDown : ChevronRight;
  return <Icon className="h-3 w-3 shrink-0 opacity-50" />;
}

/** The glyph on a row that holds other rows. A layer keeps its section icon as
 *  the anchor for its group; a concept content dir (a module with children)
 *  drops its folder glyph so the outline is not a column of identical icons.
 *  The chevron already marks it as expandable. */
function DirIcon({ node, isExpanded }: { node: TreeNode; isExpanded: boolean }) {
  if (node.path === ONBOARDING_DIR_KEY) {
    return <Compass className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)]" />;
  }
  if (hidesTreeIcon(node.page)) return null;
  if (node.page) {
    return (
      <PageIcon
        pageType={node.page.page_type}
        className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)]"
      />
    );
  }
  if (isOutlineGroupRow(node.path)) return null;
  return isExpanded ? (
    <FolderOpen className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)] opacity-70" />
  ) : (
    <Folder className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
  );
}

// The stored dotted number. The outline is numbered two levels deep ("4",
// "4.2"); deeper rows and the reference folder carry none, so a row shows its
// number whenever it has one.
function SectionNumber({ section }: { section?: string | undefined }) {
  if (!section) return null;
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
function StubDot({ page, title }: { page: DocPageSummary; title: string }) {
  if (!isStubPage(page)) return null;
  return (
    <span
      title={title}
      className="h-1.5 w-1.5 shrink-0 rounded-full ring-1 ring-[var(--color-text-tertiary)]"
    />
  );
}

/** Trailing status markers, right-aligned as one group so the two dots do not
 *  fight over `ml-auto`. */
function RowMarkers({
  page,
  showFreshness,
  stubTitle,
}: {
  page: DocPageSummary | undefined;
  showFreshness: boolean;
  stubTitle: string;
}) {
  if (!page) return null;
  return (
    <span className="ml-auto flex shrink-0 items-center gap-1 pl-1">
      <StubDot page={page} title={stubTitle} />
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
  copy,
}: DocsTreeProps) {
  const c = useMemo(() => ({ ...DEFAULT_DOCS_TREE_COPY, ...copy }), [copy]);
  const [search, setSearch] = useState("");
  // The tree filters on the settled query, so typing stays responsive on an
  // index with thousands of pages.
  const query = useDebounce(search.trim().toLowerCase(), SEARCH_DEBOUNCE_MS);
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [freshnessFilter, setFreshnessFilter] = useState<StatusFilter>("all");
  // Default to the semantic "By domain" spine — overview/architecture/modules
  // first, filesystem second. The folder view is a toggle for power users.
  const [viewMode, setViewMode] = useState<ViewMode>("domain");
  // Built before the expansion state because the defaults are read off the
  // tree's own top rung rather than guessed from the flat page list.
  const tree = useMemo(
    () => (viewMode === "domain" ? buildStoredTree(pages, c) : buildTree(pages, c)),
    [pages, viewMode, c],
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
    // Three exclusions, rows a reader opens on purpose instead of scanning: the
    // file corpus, which runs to thousands of rows, the walkthroughs, and the
    // modules no layer claimed.
    for (const node of tree) {
      if (!node.isDir) continue;
      if (SHUT_BY_DEFAULT.has(node.path)) continue;
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
    () => keepContiguousSections(filterTree(tree, query, typeFilter, freshnessFilter)),
    [tree, query, typeFilter, freshnessFilter],
  );

  // A search opens every row, so it renders a page of rows at a time.
  const searching = query.length > 0;
  // The extra pages a reader asked for belong to one query and filter set; a
  // new one starts from the first page again, on the same render.
  const resultKey = `${viewMode}|${typeFilter}|${freshnessFilter}|${query}`;
  const [extraRows, setExtraRows] = useState({ key: resultKey, rows: 0 });
  const rowLimit = SEARCH_ROW_PAGE + (extraRows.key === resultKey ? extraRows.rows : 0);
  const shown = useMemo(
    () => (searching ? capTreeRows(filteredTree, rowLimit) : null),
    [searching, filteredTree, rowLimit],
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
  const visiblePages = useMemo(
    () => pages.filter((p) => p.freshness_status !== "tombstone"),
    [pages],
  );
  const statusCounts = useMemo(() => countPageStatuses(visiblePages), [visiblePages]);
  const activeFilterCount =
    (typeFilter !== "all" ? 1 : 0) + (freshnessFilter !== "all" ? 1 : 0);

  const rows = shown ? shown.nodes : filteredTree;
  const remaining = shown ? shown.total - rowLimit : 0;

  return (
    <div className={cn("flex flex-col h-full", className)}>
      {/* Search + filter bar */}
      <div className="p-3 space-y-2 border-b border-[var(--color-border-default)]">
        <TreeSearchBar
          copy={c}
          search={search}
          setSearch={setSearch}
          viewMode={viewMode}
          toggleViewMode={() => setViewMode((m) => (m === "domain" ? "folder" : "domain"))}
          showFilters={showFilters}
          toggleFilters={() => setShowFilters((on) => !on)}
          activeFilterCount={activeFilterCount}
        />

        {showFilters && (
          <TreeFilters
            copy={c}
            typeFilter={typeFilter}
            setTypeFilter={setTypeFilter}
            freshnessFilter={freshnessFilter}
            setFreshnessFilter={setFreshnessFilter}
            showFreshness={showFreshness}
            setShowFreshness={setShowFreshness}
          />
        )}

        {/* Quiet page count, followed by only the states that need attention.
            Each one is the Status filter for that state, one click away. */}
        <StatusSummary
          copy={c}
          total={visiblePages.length}
          counts={statusCounts}
          active={freshnessFilter}
          onSelect={(status) =>
            setFreshnessFilter((current) => (current === status ? "all" : status))
          }
        />
      </div>

      <TreeRows
        copy={c}
        rows={rows}
        remaining={remaining}
        onShowMore={() => setExtraRows({ key: resultKey, rows: rowLimit })}
        item={{
          selectedPageId,
          expandedDirs,
          toggleDir,
          onSelectPage,
          forceExpand: searching,
          showFreshness: showFreshness || freshnessFilter !== "all",
          stubTitle: c.stubTitle,
        }}
      />
    </div>
  );
}

const CHIP_BASE = "rounded-full px-2 py-0.5 text-[10px] border transition-colors";

const chipClass = (selected: boolean) =>
  cn(
    CHIP_BASE,
    selected
      ? "border-[var(--color-selection-border)] bg-[var(--color-selection-wash)] text-[var(--color-text-primary)]"
      : "border-[var(--color-border-default)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]",
  );

/** A status chip takes its state's colour once chosen; "all" and "not
 *  written" have no freshness colour and take the selection wash. */
function statusChipClass(status: StatusFilter, selected: boolean): string {
  if (!selected || status === "all" || status === "unwritten") return chipClass(selected);
  return cn(CHIP_BASE, statusBadgeClasses(status));
}

function TreeFilters({
  copy: c,
  typeFilter,
  setTypeFilter,
  freshnessFilter,
  setFreshnessFilter,
  showFreshness,
  setShowFreshness,
}: {
  copy: DocsTreeCopy;
  typeFilter: TypeFilter;
  setTypeFilter: (t: TypeFilter) => void;
  freshnessFilter: StatusFilter;
  setFreshnessFilter: (f: StatusFilter) => void;
  showFreshness: boolean;
  setShowFreshness: (on: boolean) => void;
}) {
  const labelClass =
    "text-[10px] text-[var(--color-text-tertiary)] uppercase tracking-wider font-medium w-10";
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1 flex-wrap">
        <span className={labelClass}>{c.typeLabel}</span>
        {(["all", ...ALL_PAGE_TYPES] as TypeFilter[]).map((t) => (
          <button key={t} onClick={() => setTypeFilter(t)} className={chipClass(typeFilter === t)}>
            {t === "all" ? c.all : getPageTypeLabel(t)}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-1 flex-wrap">
        <span className={labelClass}>{c.statusLabel}</span>
        {STATUS_FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => setFreshnessFilter(f)}
            className={statusChipClass(f, freshnessFilter === f)}
          >
            {f === "all" ? c.all : c.status[f]}
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
        {c.showFreshnessDots}
      </label>
    </div>
  );
}

/**
 * "8,647 pages · 10 stale · 83 not written". Only states with a count render:
 * a fresh, fully written wiki reads as just its page count. Each state is a
 * toggle for the Status filter, so the summary and the filter are one control.
 */
function StatusSummary({
  copy: c,
  total,
  counts,
  active,
  onSelect,
}: {
  copy: DocsTreeCopy;
  total: number;
  counts: StatusCounts;
  active: StatusFilter;
  onSelect: (status: SummaryStatus) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
      <span>{c.pageCount(total)}</span>
      {SUMMARY_STATUSES.filter((status) => counts[status] > 0).map((status) => (
        <span key={status} className="inline-flex items-center gap-1.5">
          <span aria-hidden className="opacity-40">&middot;</span>
          <button
            type="button"
            onClick={() => onSelect(status)}
            aria-pressed={active === status}
            className={cn(
              "rounded underline-offset-2 hover:text-[var(--color-text-secondary)] hover:underline",
              active === status && "text-[var(--color-text-primary)] underline",
            )}
          >
            {c.summary[status](counts[status])}
          </button>
        </span>
      ))}
    </div>
  );
}

/** The search box and the two view toggles above the tree. */
function TreeSearchBar({
  copy: c,
  search,
  setSearch,
  viewMode,
  toggleViewMode,
  showFilters,
  toggleFilters,
  activeFilterCount,
}: {
  copy: DocsTreeCopy;
  search: string;
  setSearch: (q: string) => void;
  viewMode: ViewMode;
  toggleViewMode: () => void;
  showFilters: boolean;
  toggleFilters: () => void;
  activeFilterCount: number;
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1.5">
        <Search className="h-3.5 w-3.5 text-[var(--color-text-tertiary)] shrink-0" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={c.searchPlaceholder}
          aria-label={c.searchPlaceholder}
          className="flex-1 min-w-0 bg-transparent text-xs text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] outline-none"
        />
      </div>
      {/* View switch — domain is the default reading spine; folder is the
          power-user escape hatch, so it rides as a single quiet toggle
          rather than a full-width band. */}
      <button
        onClick={toggleViewMode}
        aria-label={viewMode === "domain" ? c.switchToFolderView : c.switchToDomainView}
        title={viewMode === "domain" ? c.folderView : c.domainView}
        className="rounded-md p-1.5 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-secondary)]"
      >
        {viewMode === "domain" ? (
          <FolderTree className="h-3.5 w-3.5" />
        ) : (
          <Network className="h-3.5 w-3.5" />
        )}
      </button>
      <button
        onClick={toggleFilters}
        aria-label={c.toggleFilters}
        aria-expanded={showFilters}
        className={cn(
          "relative rounded-md p-1.5 transition-colors",
          showFilters || activeFilterCount > 0
            ? "bg-[var(--color-selection-wash)] text-[var(--color-text-primary)]"
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
  );
}

type TreeItemShared = Omit<Parameters<typeof TreeItem>[0], "node" | "depth">;

/** The rendered outline, or the empty state, plus the row that pages a capped
 *  search result forward. */
function TreeRows({
  copy: c,
  rows,
  remaining,
  onShowMore,
  item,
}: {
  copy: DocsTreeCopy;
  rows: TreeNode[];
  remaining: number;
  onShowMore: () => void;
  item: TreeItemShared;
}) {
  return (
    <div className="flex-1 overflow-y-auto p-1.5">
      {rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-32 text-xs text-[var(--color-text-tertiary)]">
          <p>{c.noMatches}</p>
        </div>
      ) : (
        <div className="space-y-1">
          {rows.map((node) => (
            <TreeItem key={node.path} node={node} depth={0} {...item} />
          ))}
          {remaining > 0 && (
            <button
              type="button"
              onClick={onShowMore}
              className="w-full rounded-md px-2 py-1.5 text-left text-xs text-[var(--color-accent-primary)] hover:bg-[var(--color-bg-elevated)]"
            >
              {c.showMore(Math.min(SEARCH_ROW_PAGE, remaining), remaining)}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
