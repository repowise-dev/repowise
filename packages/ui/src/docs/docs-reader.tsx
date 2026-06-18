"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  FileText,
  Clock,
  Cpu,
  ArrowRight,
  ArrowLeft,
  Loader2,
  Layers,
  FileInput,
} from "lucide-react";
import type { DocPage } from "@repowise-dev/types/docs";
import { cn } from "../lib/cn";
import { formatRelativeTime, formatTokens } from "../lib/format";
import { getPageTypeLabel } from "../lib/page-types";
import { computeDocNav } from "./doc-nav";
import { filterMarkdownByPersona, type ReaderPersona } from "./reader-persona";
import { WikiMarkdown } from "../wiki/wiki-markdown";
import { TableOfContents } from "../wiki/table-of-contents";
import { BacklinksPanel } from "../wiki/backlinks-panel";
import {
  getBacklinks,
  getWikiLinks,
} from "../wiki/wiki-links-types";
import { Breadcrumb } from "../shared/breadcrumb";

/** Router-aware anchor — host injects Next.js Link / in-app interception. */
export type ReaderLinkComponent = React.ElementType<{
  href: string;
  className?: string;
  title?: string;
  children: React.ReactNode;
}>;

interface DocsReaderProps {
  page: DocPage | null;
  /** Full page list — powers hierarchical breadcrumbs and prev/next. */
  pages?: DocPage[];
  repoId: string;
  isLoading?: boolean;
  /** Select another page in-place (breadcrumb / prev-next / wiki links). */
  onSelectPage?: (page: DocPage) => void;
  /** Navigate by page id (resolved wiki links / backlinks fall through here). */
  onNavigatePageId?: (pageId: string) => void;
  persona: ReaderPersona;
  sidebarOpen: boolean;
  /** ``?page=`` href builder — host owns the route shape. */
  buildPageHref: (pageId: string) => string;
  /** Router-aware link for in-content + breadcrumb anchors. */
  LinkComponent: ReaderLinkComponent;
  /**
   * Data-bound rail sections (graph intelligence, git "at a glance", security)
   * that require host hooks. Rendered below the on-page contents + provenance.
   */
  intelligenceSlot?: React.ReactNode;
  /** Data-bound version history (host owns the SWR fetch). */
  versionHistorySlot?: React.ReactNode;
}

export function DocsReader({
  page,
  pages = [],
  repoId,
  isLoading,
  onSelectPage,
  onNavigatePageId,
  persona,
  sidebarOpen,
  buildPageHref,
  LinkComponent,
  intelligenceSlot,
  versionHistorySlot,
}: DocsReaderProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const goToPageId = useCallback(
    (pageId: string) => {
      const target = pages.find((p) => p.id === pageId);
      if (target && onSelectPage) onSelectPage(target);
      else if (onNavigatePageId) onNavigatePageId(pageId);
    },
    [pages, onSelectPage, onNavigatePageId],
  );

  useEffect(() => {
    scrollRef.current?.scrollTo(0, 0);
  }, [page?.id]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-5 w-5 animate-spin text-[var(--color-accent-primary)]" />
      </div>
    );
  }

  if (!page) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
        <div className="rounded-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)] p-4">
          <FileText className="h-8 w-8 text-[var(--color-text-tertiary)]" />
        </div>
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Select a page
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)] max-w-sm">
            Choose a file or module from the tree to view its AI-generated documentation.
          </p>
        </div>
      </div>
    );
  }

  return (
    <DocsReaderBody
      page={page}
      pages={pages}
      repoId={repoId}
      sidebarOpen={sidebarOpen}
      scrollRef={scrollRef}
      goToPageId={goToPageId}
      persona={persona}
      buildPageHref={buildPageHref}
      LinkComponent={LinkComponent}
      intelligenceSlot={intelligenceSlot}
      versionHistorySlot={versionHistorySlot}
    />
  );
}

function DocsReaderBody({
  page,
  pages,
  repoId,
  sidebarOpen,
  scrollRef,
  goToPageId,
  persona,
  buildPageHref,
  LinkComponent,
  intelligenceSlot,
  versionHistorySlot,
}: {
  page: DocPage;
  pages: DocPage[];
  repoId: string;
  sidebarOpen: boolean;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  goToPageId: (pageId: string) => void;
  persona: ReaderPersona;
  buildPageHref: (pageId: string) => string;
  LinkComponent: ReaderLinkComponent;
  intelligenceSlot?: React.ReactNode;
  versionHistorySlot?: React.ReactNode;
}) {
  const nav = useMemo(() => computeDocNav(page, pages), [page, pages]);
  const wikiLinks = useMemo(() => getWikiLinks(page.metadata), [page.metadata]);

  const visibleContent = useMemo(
    () => filterMarkdownByPersona(page.content, persona),
    [page.content, persona],
  );

  const moduleSeg = useMemo(
    () =>
      [...nav.breadcrumbs]
        .slice(0, -1)
        .reverse()
        .find((s) => s.pageId && s.pageId !== page.id),
    [nav.breadcrumbs, page.id],
  );

  const relatedLinks = useMemo(() => {
    const byId = new Map(pages.map((p) => [p.id, p]));
    const seen = new Set<string>();
    const out: { id: string; title: string }[] = [];
    for (const link of wikiLinks) {
      const target = link.target_page_id;
      if (target === page.id || seen.has(target)) continue;
      const hit = byId.get(target);
      if (!hit) continue;
      seen.add(target);
      out.push({ id: hit.id, title: hit.title });
    }
    return out;
  }, [wikiLinks, pages, page.id]);

  const layerName =
    typeof page.metadata?.layer_name === "string" ? page.metadata.layer_name : "";
  const layerId =
    typeof page.metadata?.layer_id === "string" ? page.metadata.layer_id : "";
  const layerPage = useMemo(
    () =>
      layerId
        ? pages.find(
            (p) => p.page_type === "layer_page" && p.target_path === layerId,
          ) ??
          (layerName
            ? pages.find(
                (p) =>
                  p.page_type === "layer_page" &&
                  p.target_path === `layer:${layerName}`,
              )
            : undefined)
        : layerName
          ? pages.find(
              (p) =>
                p.page_type === "layer_page" &&
                p.target_path === `layer:${layerName}`,
            )
          : undefined,
    [pages, layerId, layerName],
  );

  const sources = useMemo(() => {
    const raw = page.metadata?.sources;
    if (!Array.isArray(raw)) return [];
    const byPath = new Map(pages.map((p) => [p.target_path, p]));
    return (raw as Array<{ path?: string; kind?: string }>)
      .map((s) => {
        const path = typeof s?.path === "string" ? s.path : "";
        return { path, kind: s?.kind ?? "", pageId: byPath.get(path)?.id };
      })
      .filter((s) => s.path);
  }, [page.metadata, pages]);

  // Resolved wiki link: a real href (middle-click opens in a new tab) with
  // plain clicks intercepted for in-app nav.
  const WikiInlineLink = useMemo(() => {
    function Comp({
      href,
      className,
      title,
      children,
    }: {
      href: string;
      className?: string;
      title?: string;
      children: React.ReactNode;
    }) {
      return (
        <a
          href={href}
          className={className}
          title={title}
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
            try {
              const u = new URL(href, window.location.origin);
              const pid = u.searchParams.get("page");
              if (pid) {
                e.preventDefault();
                goToPageId(pid);
              }
            } catch {
              /* fall through to default navigation */
            }
          }}
        >
          {children}
        </a>
      );
    }
    return Comp;
  }, [goToPageId]);

  return (
    <div className="flex h-full">
      <div className="flex flex-col flex-1 min-w-0">
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="px-4 sm:px-6 py-8 max-w-[768px] mx-auto">
            {/* Hierarchical breadcrumb */}
            <div className="mb-3 overflow-hidden">
              <Breadcrumb
                segments={nav.breadcrumbs.map((seg) => ({
                  label: seg.label,
                  ...(seg.pageId && seg.pageId !== page.id
                    ? { href: buildPageHref(seg.pageId) }
                    : {}),
                }))}
                LinkComponent={WikiInlineLink}
              />
            </div>

            {/* Title */}
            <h1 className="font-serif text-[2rem] leading-tight font-semibold tracking-tight text-[var(--color-text-primary)] mb-2 break-words">
              {page.title}
            </h1>

            {/* One calm metadata line: type + module + layer + freshness +
                version + model — every metadata fact stated once, here. */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[10px] text-[var(--color-text-tertiary)] mb-6">
              <span className="rounded-full bg-[var(--color-bg-elevated)] px-2 py-0.5 uppercase tracking-wider">
                {getPageTypeLabel(page.page_type)}
              </span>
              {moduleSeg && (
                <button
                  onClick={() => goToPageId(moduleSeg.pageId!)}
                  className="rounded-full border border-[var(--color-border-default)] px-2 py-0.5 text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-accent)] hover:text-[var(--color-accent-primary)]"
                >
                  in {moduleSeg.label}
                </button>
              )}
              {layerName &&
                (layerPage ? (
                  <button
                    onClick={() => goToPageId(layerPage.id)}
                    className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border-default)] px-2 py-0.5 text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-accent)] hover:text-[var(--color-accent-primary)]"
                  >
                    <Layers className="h-2.5 w-2.5" />
                    {layerName}
                  </button>
                ) : (
                  <span className="inline-flex items-center gap-1 rounded-full bg-[var(--color-bg-elevated)] px-2 py-0.5">
                    <Layers className="h-2.5 w-2.5" />
                    {layerName}
                  </span>
                ))}
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {formatRelativeTime(page.updated_at)}
              </span>
              <span>v{page.version}</span>
              <span className="font-mono">
                {formatTokens(page.input_tokens)} in · {formatTokens(page.output_tokens)} out
              </span>
              {page.model_name && (
                <span className="flex items-center gap-1 font-mono">
                  <Cpu className="h-3 w-3" />
                  {page.model_name}
                </span>
              )}
            </div>

            {/* Low-confidence flag */}
            {page.confidence > 0 && page.confidence < 0.5 && (
              <div className="mb-4 flex items-start gap-1.5 rounded-md border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 px-3 py-2">
                <span className="text-xs text-[var(--color-text-primary)]">
                  This page was generated with low confidence — verify against the source before relying on it.
                </span>
              </div>
            )}

            {/* Human notes (read-only callout; editing lives in the rail) */}
            {page.human_notes && (
              <div className="mb-4 rounded-lg border border-[var(--color-border-accent)] bg-[var(--color-accent-blue)]/5 px-4 py-3">
                <p className="text-sm text-[var(--color-text-secondary)] whitespace-pre-wrap leading-relaxed">
                  {page.human_notes}
                </p>
              </div>
            )}

            {/* Markdown content */}
            <article className="prose prose-invert max-w-none leading-relaxed overflow-hidden">
              <WikiMarkdown
                content={visibleContent}
                wikiLinks={wikiLinks}
                buildHref={(pid) => buildPageHref(pid)}
                LinkComponent={WikiInlineLink}
              />
            </article>

            {/* Sibling prev / next */}
            {(nav.prev || nav.next) && (
              <nav className="mt-8 flex items-stretch gap-3 border-t border-[var(--color-border-default)] pt-4">
                {nav.prev ? (
                  <button
                    onClick={() => goToPageId(nav.prev!.pageId)}
                    className="group flex flex-1 items-center gap-2 rounded-lg border border-[var(--color-border-default)] px-3 py-2 text-left transition-colors hover:border-[var(--color-border-accent)] hover:bg-[var(--color-bg-elevated)]"
                  >
                    <ArrowLeft className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] group-hover:text-[var(--color-accent-primary)]" />
                    <span className="min-w-0">
                      <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                        Previous
                      </span>
                      <span className="block truncate text-xs text-[var(--color-text-secondary)]">
                        {nav.prev.title}
                      </span>
                    </span>
                  </button>
                ) : (
                  <span className="flex-1" />
                )}
                {nav.next && (
                  <button
                    onClick={() => goToPageId(nav.next!.pageId)}
                    className="group flex flex-1 items-center justify-end gap-2 rounded-lg border border-[var(--color-border-default)] px-3 py-2 text-right transition-colors hover:border-[var(--color-border-accent)] hover:bg-[var(--color-bg-elevated)]"
                  >
                    <span className="min-w-0">
                      <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                        Next
                      </span>
                      <span className="block truncate text-xs text-[var(--color-text-secondary)]">
                        {nav.next.title}
                      </span>
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] group-hover:text-[var(--color-accent-primary)]" />
                  </button>
                )}
              </nav>
            )}

            {/* Version history (host-supplied data wrapper) */}
            {versionHistorySlot && <div className="mt-8">{versionHistorySlot}</div>}

            {/* Metadata warnings */}
            {Array.isArray(page.metadata?.hallucination_warnings) &&
              (page.metadata.hallucination_warnings as string[]).length > 0 && (
                <div className="mt-4 rounded-lg border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 px-4 py-3">
                  <p className="text-xs font-medium text-[var(--color-warning)] mb-1.5">
                    Possible inaccuracies detected
                  </p>
                  <ul className="space-y-0.5">
                    {(page.metadata.hallucination_warnings as string[]).map((w, i) => (
                      <li key={i} className="text-xs text-[var(--color-text-secondary)] font-mono">
                        {String(w)}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
          </div>
        </div>
      </div>

      {/* Right intelligence rail — on-page contents, provenance, related,
          backlinks, then host-supplied data-bound intelligence sections. */}
      {sidebarOpen && (
        <div className="hidden lg:block border-l border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shrink-0 w-[260px] overflow-auto">
          <div className="space-y-6 p-4">
            <TableOfContents content={page.content} />
            {sources.length > 0 && (
              <div>
                <div className="flex items-center gap-1.5 mb-2">
                  <FileInput className="h-3 w-3 text-[var(--color-text-tertiary)]" />
                  <span className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                    Built from
                  </span>
                </div>
                <ul className="space-y-1">
                  {sources.slice(0, 5).map((s) => (
                    <li key={s.path} className="text-xs">
                      {s.pageId ? (
                        <button
                          onClick={() => goToPageId(s.pageId!)}
                          className="truncate text-left font-mono text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] transition-colors w-full"
                          title={`${s.path} (${s.kind})`}
                        >
                          {s.path.split("/").pop()}
                        </button>
                      ) : (
                        <span
                          className="block truncate font-mono text-[var(--color-text-tertiary)]"
                          title={`${s.path} (${s.kind})`}
                        >
                          {s.path.split("/").pop()}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
                {sources.length > 5 && (
                  <p className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">
                    + {sources.length - 5} more
                  </p>
                )}
              </div>
            )}
            {relatedLinks.length > 0 && (
              <div>
                <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
                  Related
                </p>
                <ul className="space-y-1.5">
                  {relatedLinks.slice(0, 5).map((r) => (
                    <li key={r.id} className="text-xs">
                      <button
                        onClick={() => goToPageId(r.id)}
                        className="truncate text-left text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] transition-colors w-full"
                        title={r.title}
                      >
                        {r.title}
                      </button>
                    </li>
                  ))}
                </ul>
                {relatedLinks.length > 5 && (
                  <p className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">
                    + {relatedLinks.length - 5} more
                  </p>
                )}
              </div>
            )}
            <BacklinksPanel
              backlinks={getBacklinks(page.metadata)}
              repoId={repoId}
              buildHref={(_rid, pid) => buildPageHref(pid)}
              renderLink={({ href, className, title, children }) => (
                <LinkComponent href={href} className={className} title={title}>
                  {children}
                </LinkComponent>
              )}
            />
            {intelligenceSlot}
          </div>
        </div>
      )}
    </div>
  );
}
