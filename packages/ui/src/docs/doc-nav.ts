// Framework-neutral navigation helpers for the wiki reader.
//
// The breadcrumb trail and the sibling prev/next links are read from the
// stored page tree (``parent_page_id`` / ``display_order``), the same one the
// docs tree renders and ``get_overview`` serves, so a page's ancestry is the
// same wherever it is shown. It used to be re-derived here by splitting
// ``target_path`` and matching module pages by path prefix, which was a third
// independent guess at the hierarchy.
//
// A store written before the tree existed has no parents at all; those pages
// fall back to the path split, so an un-rebuilt wiki keeps working breadcrumbs
// rather than losing them until it is re-indexed.
//
// The host app turns the returned ``pageId`` values into hrefs (route shape
// differs between the CLI web app and hosted frontend).

import type { DocPage } from "@repowise-dev/types/docs";
import { treeLabel } from "./page-labels";

export interface DocNavSegment {
  label: string;
  /** Present when this segment maps to a real page that can be linked. */
  pageId?: string;
  /**
   * Page type of the linked page. Ancestors in the stored tree are whatever
   * the tree put there — a module, but equally a layer or the file a symbol
   * was spotted in — so a caller that wants "the module this sits in" has to
   * ask rather than assume the second-to-last crumb is one.
   */
  pageType?: string;
}

export interface DocSiblingLink {
  pageId: string;
  title: string;
}

export interface DocNavInfo {
  /** repo root is added by the caller; these are page-relative segments. */
  breadcrumbs: DocNavSegment[];
  prev?: DocSiblingLink;
  next?: DocSiblingLink;
}

function parentDir(path: string): string {
  const i = path.lastIndexOf("/");
  return i === -1 ? "" : path.slice(0, i);
}

/** Ancestors of ``page``, outermost first, excluding the root and the page. */
function ancestors(page: DocPage, byId: Map<string, DocPage>): DocPage[] {
  const chain: DocPage[] = [];
  const seen = new Set<string>([page.id]);
  let current = byId.get(page.parent_page_id ?? "");
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    // The repo root is the crumb the caller prepends; repeating it here would
    // show the repository name twice.
    if (current.parent_page_id) chain.unshift(current);
    current = byId.get(current.parent_page_id ?? "");
  }
  return chain;
}

/** The label a page carries in a trail — the same one the docs tree shows. */
function crumbLabel(page: DocPage): string {
  return treeLabel(page, undefined);
}

/**
 * Breadcrumbs + sibling prev/next for ``page`` given the full page list.
 *
 * Ancestors are walked up ``parent_page_id`` and each links to a real page, so
 * a reader can zoom out one documented level at a time — a module, then the
 * layer it sits in — rather than through directory names that may have no page
 * behind them.
 */
export function computeDocNav(page: DocPage, pages: DocPage[]): DocNavInfo {
  const byId = new Map(pages.map((p) => [p.id, p]));
  const chain = page.parent_page_id ? ancestors(page, byId) : [];

  // Only when there is a documented ancestor to show. A page hanging directly
  // off the repo root has none, and a lone basename crumb would say less than
  // the directory trail the path still carries, so those keep the path split.
  if (chain.length > 0) {
    const breadcrumbs: DocNavSegment[] = [
      ...chain.map((a) => ({
        label: crumbLabel(a),
        pageId: a.id,
        pageType: a.page_type,
      })),
      { label: crumbLabel(page), pageId: page.id, pageType: page.page_type },
    ];

    // Siblings are the pages sharing this parent, in stored order. Unlike the
    // old same-type/same-directory rule, "next" walks the outline the reader
    // is actually looking at.
    const siblings = pages
      .filter((p) => p.parent_page_id === page.parent_page_id)
      .sort(
        (a, b) =>
          (a.display_order ?? 0) - (b.display_order ?? 0) ||
          (a.target_path || a.title).localeCompare(b.target_path || b.title),
      );
    return { breadcrumbs, ...siblingLinks(siblings, page) };
  }

  // No stored parent: either a root/synthetic page, or a store predating the
  // tree. Special pages (overview, onboarding, architecture) have no
  // meaningful filesystem path — show a single label, no siblings.
  if (!page.target_path) {
    return { breadcrumbs: [{ label: page.title }] };
  }

  // A layer or cycle hanging off the root carries a synthetic target_path
  // ("layer:ui", "scc-103") rather than a directory trail. Splitting it would
  // show that raw id as a crumb; use the same clean label the tree does. The
  // pageId is the current page's own, so the reader renders it as plain text
  // (it only links crumbs whose pageId differs from the page).
  if (!page.target_path.includes("/")) {
    return { breadcrumbs: [{ label: crumbLabel(page), pageId: page.id }] };
  }

  // Index module pages by the directory they represent so an ancestor
  // directory segment can deep-link to its module overview.
  const moduleByPath = new Map<string, DocPage>();
  for (const p of pages) {
    if (p.page_type === "module_page" && p.target_path) {
      moduleByPath.set(p.target_path, p);
    }
  }

  const parts = page.target_path.split("/").filter(Boolean);
  const breadcrumbs: DocNavSegment[] = [];
  for (let i = 0; i < parts.length; i++) {
    const segPath = parts.slice(0, i + 1).join("/");
    const isLeaf = i === parts.length - 1;
    const mod = moduleByPath.get(segPath);
    breadcrumbs.push({
      label: parts[i] ?? segPath,
      // The leaf is the current page; ancestors link to their module page
      // when we have one.
      ...(isLeaf
        ? { pageId: page.id }
        : mod
          ? { pageId: mod.id }
          : {}),
    });
  }

  const dir = parentDir(page.target_path);
  const siblings = pages
    .filter(
      (p) =>
        p.page_type === page.page_type &&
        p.target_path &&
        parentDir(p.target_path) === dir,
    )
    .sort((a, b) => a.target_path.localeCompare(b.target_path));

  return { breadcrumbs, ...siblingLinks(siblings, page) };
}

function siblingLinks(
  siblings: DocPage[],
  page: DocPage,
): { prev?: DocSiblingLink; next?: DocSiblingLink } {
  const idx = siblings.findIndex((p) => p.id === page.id);
  if (idx === -1) return {};
  const prev = siblings[idx - 1];
  const next = siblings[idx + 1];
  return {
    ...(prev ? { prev: { pageId: prev.id, title: prev.title } } : {}),
    ...(next ? { next: { pageId: next.id, title: next.title } } : {}),
  };
}
