// Display labels for wiki pages — replace raw graph ids with derived names.
//
// Generation titles carry raw graph ids ("Module: community-207",
// "Circular Dependency: scc-103"). The human-readable names live on the page
// itself — module titles already carry the derived path, and SCC members are
// listed in the cycle description — so labels are derived client-side instead
// of leaking ids. metadata.derived_label, when present, always wins
// (future-proofing for generation-side labels).
//
// This lives apart from the tree component because the breadcrumb trail
// (`doc-nav.ts`, framework-neutral) needs the same labels and must not pull in
// React to get them.

import type { DocPage } from "@repowise-dev/types/docs";

export const RAW_GRAPH_ID = /^(?:community|scc)[-_\s]?\d+$/i;

export function sccDisplayLabel(page: DocPage): string {
  // Cycle members appear back-ticked in the generated description; prefer
  // the "Files Involved" section when present so prose mentions don't leak in.
  const involved = page.content.split(/^##\s+Files Involved\s*$/im)[1];
  const section = involved ? involved.split(/^##\s/m)[0] ?? involved : page.content;
  const paths = [...section.matchAll(/`([^`\s]+\/[^`\s]+)`/g)].map((m) => m[1]!);
  if (paths.length === 0) return page.title;

  // Common directory of the members, shown as its last two segments —
  // "Cycle: generation/page_generator" reads better than "scc-103".
  let common = (paths[0] ?? "").split("/").slice(0, -1);
  for (const p of paths.slice(1)) {
    const parts = p.split("/").slice(0, -1);
    let i = 0;
    while (i < common.length && i < parts.length && common[i] === parts[i]) i++;
    common = common.slice(0, i);
  }
  const dir = common.slice(-2).join("/");
  return dir ? `Cycle: ${dir}` : page.title;
}

// Page types whose target_path is a real file. Everything else is named by its
// title (or, for module / layer / cycle pages, the label derived from it): an
// overview's path is the repo name and an onboarding page's is a slot, and
// neither reads as a row label.
const PATH_NAMED_TYPES = new Set(["file_page", "api_contract", "infra_page"]);

/**
 * What a page is called in a tree row or a breadcrumb, given the page it hangs
 * off. A file under its module is named relative to it, so three files called
 * index.py in different resolver directories stay distinguishable.
 */
export function treeLabel(page: DocPage, parent: DocPage | undefined): string {
  const path = page.target_path;
  if (page.page_type === "symbol_spotlight" && path.includes("::")) {
    // "path/to/file.py::Symbol" — the file is the parent row, so the symbol is
    // the only part that carries information here.
    return path.split("::")[1] || page.title;
  }
  if (!path || !PATH_NAMED_TYPES.has(page.page_type)) return displayLabel(page);
  const parentPath = parent?.target_path;
  if (parentPath && path.startsWith(parentPath + "/")) {
    return path.slice(parentPath.length + 1);
  }
  return path.split("/").pop() || path;
}

export function displayLabel(page: DocPage): string {
  const metaLabel = page.metadata?.["derived_label"];
  if (typeof metaLabel === "string" && metaLabel) return metaLabel;

  if (page.page_type === "module_page") {
    const name = page.title.replace(/^Module:\s*/i, "");
    if (name && !RAW_GRAPH_ID.test(name)) return name;
    const fallback = page.target_path.split("/").pop() ?? page.target_path;
    return RAW_GRAPH_ID.test(fallback) ? page.title : fallback;
  }
  if (page.page_type === "scc_page") return sccDisplayLabel(page);
  if (page.page_type === "layer_page") return page.title.replace(/^Layer:\s*/i, "");
  return page.title;
}
