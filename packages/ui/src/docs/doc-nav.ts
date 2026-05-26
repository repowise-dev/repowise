// Framework-neutral navigation helpers for the wiki reader.
//
// These derive a hierarchical breadcrumb trail (repo / module / file) and
// sibling prev/next links from the flat page list the API already returns —
// no extra endpoint needed. The host app turns the returned ``pageId`` values
// into hrefs (route shape differs between the CLI web app and hosted frontend).

import type { DocPage } from "@repowise-dev/types/docs";

export interface DocNavSegment {
  label: string;
  /** Present when this segment maps to a real page that can be linked. */
  pageId?: string;
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

/**
 * Derive breadcrumbs + sibling prev/next for ``page`` given the full page
 * list. Breadcrumb ancestors link to their ``module_page`` when one exists,
 * so readers can zoom out a level at a time.
 */
export function computeDocNav(page: DocPage, pages: DocPage[]): DocNavInfo {
  // Special / synthetic pages (overview, onboarding, architecture) have no
  // meaningful filesystem path — show a single label, no siblings.
  if (!page.target_path) {
    return { breadcrumbs: [{ label: page.title }] };
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

  // Sibling prev/next: pages of the same type sharing the immediate parent
  // directory, ordered by path. Keeps "next" within a coherent group rather
  // than jumping across unrelated page types.
  const dir = parentDir(page.target_path);
  const siblings = pages
    .filter(
      (p) =>
        p.page_type === page.page_type &&
        p.target_path &&
        parentDir(p.target_path) === dir,
    )
    .sort((a, b) => a.target_path.localeCompare(b.target_path));

  const idx = siblings.findIndex((p) => p.id === page.id);
  const result: DocNavInfo = { breadcrumbs };
  if (idx !== -1) {
    const prev = siblings[idx - 1];
    const next = siblings[idx + 1];
    if (prev) result.prev = { pageId: prev.id, title: prev.title };
    if (next) result.next = { pageId: next.id, title: next.title };
  }
  return result;
}
