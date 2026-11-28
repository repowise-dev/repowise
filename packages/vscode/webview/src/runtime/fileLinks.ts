/**
 * File links for shared components that take a `fileHref` callback.
 *
 * The web app hands those components a route to its file page. A webview has
 * no routes, and a real navigation would replace the panel, so the href here
 * encodes the path (and line) under a custom scheme, and one document-level
 * listener turns a click on such an anchor into `host.openFile`. Document
 * level, not a wrapper's onClick, because drawers and dialogs render in a
 * portal outside the view's own subtree.
 */

import { useEffect } from "react";
import type { WebviewHost } from "./rpc";

const FILE_HREF_PREFIX = "repowise-open:";

/** An href that opens `path` (at `line`) in an editor column when clicked. */
export function fileHref(path: string, line?: number | null): string {
  const query = line != null ? `?line=${line}` : "";
  return `${FILE_HREF_PREFIX}${encodeURIComponent(path)}${query}`;
}

/** The path and line an href from {@link fileHref} carries, or null. */
export function parseFileHref(href: string): { path: string; line?: number } | null {
  if (!href.startsWith(FILE_HREF_PREFIX)) return null;
  const [encodedPath, query] = href.slice(FILE_HREF_PREFIX.length).split("?");
  const path = decodeURIComponent(encodedPath ?? "");
  if (!path) return null;
  const line = query?.startsWith("line=") ? Number(query.slice("line=".length)) : NaN;
  return Number.isFinite(line) ? { path, line } : { path };
}

/** Route clicks on {@link fileHref} anchors anywhere in the document to the editor. */
export function useFileLinks(host: WebviewHost): void {
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const anchor = (event.target as Element | null)?.closest?.("a");
      const target = anchor ? parseFileHref(anchor.getAttribute("href") ?? "") : null;
      if (!target) return;
      event.preventDefault();
      host.openFile(target.path, target.line);
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [host]);
}
