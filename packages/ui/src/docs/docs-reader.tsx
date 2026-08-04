"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  FileText,
  ArrowRight,
  ArrowLeft,
  ChevronRight,
  Layers,
} from "lucide-react";
import type { DocPage, DocPageSummary } from "@repowise-dev/types/docs";
import { cn } from "../lib/cn";
import { formatRelativeTime, formatTokens } from "../lib/format";
import { getPageLabel } from "../lib/page-types";
import { computeDocNav } from "./doc-nav";
import { filterMarkdownByPersona, type ReaderPersona } from "./reader-persona";
import { WikiMarkdown } from "../wiki/wiki-markdown";
import { TableOfContents } from "../wiki/table-of-contents";
import { BacklinksPanel } from "../wiki/backlinks-panel";
import {
  getBacklinks,
  getRelatedPages,
  getWikiLinks,
  type RelatedReason,
} from "../wiki/wiki-links-types";
import { Breadcrumb } from "../shared/breadcrumb";
import { Skeleton } from "../ui/skeleton";

/** Related entries shown before the "+ N more" line. Five, not eight: the list
 *  is a suggestion of where to go next, and past about five it reads as a dump
 *  of everything the graph knows. */
const RELATED_LIMIT = 5;

const RELATED_REASON_LABELS: Record<RelatedReason, string> = {
  imports: "imports",
  "imported-by": "imported by",
  "co-changes-with": "changes together",
  "same-module": "same module",
};

// Remove a leading level-1 heading whose text is exactly the page title. The
// title is already rendered above the body, so this heading is a duplicate.
// Only the first heading is considered and only on an exact (case-insensitive)
// match, so a section that legitimately reuses the title text is never cut.
function stripLeadingTitleHeading(content: string, title: string): string {
  const wanted = title.trim().toLowerCase();
  if (!wanted) return content;
  const lines = content.split("\n");
  let i = 0;
  while (i < lines.length && lines[i]!.trim() === "") i++;
  const heading = lines[i]?.match(/^#\s+(.+?)\s*$/);
  if (!heading || heading[1]!.trim().toLowerCase() !== wanted) return content;
  lines.splice(0, i + 1);
  while (lines.length > 0 && lines[0]!.trim() === "") lines.shift();
  return lines.join("\n");
}

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
  pages?: DocPageSummary[];
  repoId: string;
  isLoading?: boolean;
  /** Select another page in-place (breadcrumb / prev-next / wiki links). */
  onSelectPage?: (page: DocPageSummary) => void;
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
  /**
   * A compact "Write with AI" affordance rendered inline in the metadata row,
   * beside the "Auto" pill, on a template page. Host owns the launch; omit it
   * on AI-written pages so only auto pages advertise the upgrade.
   */
  upgradeSlot?: React.ReactNode;
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
  upgradeSlot,
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

  if (isLoading) return <ReaderSkeleton />;

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
      upgradeSlot={upgradeSlot}
    />
  );
}

/**
 * The reading column while its page is being fetched.
 *
 * Same wrapper, same 720px column, same rhythm: breadcrumb, title, provenance
 * line, prose. A centred spinner used to sit here, which collapsed the layout
 * to nothing and reflowed the whole column when the page landed.
 */
function ReaderSkeleton() {
  return (
    <div className="flex h-full" aria-busy="true">
      <div className="flex flex-col flex-1 min-w-0">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[720px] px-4 py-8 sm:px-6">
            <Skeleton className="mb-3 h-3 w-52 rounded" />
            <Skeleton className="mb-2 h-9 w-2/3 rounded" />
            <Skeleton className="mb-5 h-3 w-44 rounded" />
            {/* Literal widths, not interpolated ones — Tailwind only ships the
                classes it can see in the source. */}
            <div className="flex flex-col gap-3">
              {[
                "w-full",
                "w-11/12",
                "w-5/6",
                "w-full",
                "w-3/4",
                "w-full",
                "w-2/3",
              ].map((w, i) => (
                <Skeleton key={i} className={`h-4 rounded ${w}`} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
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
  upgradeSlot,
}: {
  page: DocPage;
  pages: DocPageSummary[];
  repoId: string;
  sidebarOpen: boolean;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  goToPageId: (pageId: string) => void;
  persona: ReaderPersona;
  buildPageHref: (pageId: string) => string;
  LinkComponent: ReaderLinkComponent;
  intelligenceSlot?: React.ReactNode;
  versionHistorySlot?: React.ReactNode;
  upgradeSlot?: React.ReactNode;
}) {
  const nav = useMemo(() => computeDocNav(page, pages), [page, pages]);
  const wikiLinks = useMemo(() => getWikiLinks(page.metadata), [page.metadata]);

  // A page with no model behind it. Structural pages are templates by design
  // and always will be; a model-written page that is still a template has
  // prose outstanding, which is what the upgrade affordance is for.
  const isTemplatePage = page.provider_name === "template";

  // The reader renders the page title as the H1 above the body, but generated
  // content often opens with its own "# <title>" line (the deterministic
  // templates do), so the same heading shows twice. Drop a leading H1 that
  // exactly matches the title before anything else reads the content.
  const bodyContent = useMemo(
    () => stripLeadingTitleHeading(page.content, page.title),
    [page.content, page.title],
  );

  const visibleContent = useMemo(
    () => filterMarkdownByPersona(bodyContent, persona),
    [bodyContent, persona],
  );

  // The nearest ancestor that is actually a module. Breadcrumbs now come from
  // the stored tree, whose ancestors can be a layer or the file a symbol was
  // spotted in; showing either as "in <name>" would repeat the layer chip
  // rendered right beside it, or claim a file is a module.
  const moduleSeg = useMemo(
    () =>
      [...nav.breadcrumbs]
        .slice(0, -1)
        .reverse()
        .find(
          (s) =>
            s.pageId &&
            s.pageId !== page.id &&
            // Older callers (and the path-split fallback) carry no page type;
            // those segments were always module-or-directory, so they stand.
            (s.pageType === undefined || s.pageType === "module_page"),
        ),
    [nav.breadcrumbs, page.id],
  );

  const relatedLinks = useMemo(() => {
    const byId = new Map(pages.map((p) => [p.id, p]));
    const seen = new Set<string>();
    const out: { id: string; title: string; reason?: RelatedReason }[] = [];
    // Graph-derived neighbors first — they carry a reason and exist even
    // when the prose never mentions the target. The backend dedups them
    // against wiki_links, but stay defensive here.
    for (const rel of getRelatedPages(page.metadata)) {
      const target = rel.target_page_id;
      if (target === page.id || seen.has(target)) continue;
      const hit = byId.get(target);
      if (!hit) continue;
      seen.add(target);
      out.push({ id: hit.id, title: hit.title, reason: rel.reason });
    }
    for (const link of wikiLinks) {
      const target = link.target_page_id;
      if (target === page.id || seen.has(target)) continue;
      const hit = byId.get(target);
      if (!hit) continue;
      seen.add(target);
      out.push({ id: hit.id, title: hit.title });
    }
    return out;
  }, [wikiLinks, page.metadata, pages, page.id]);

  // layer_name is display text only. Joining to the layer page goes through
  // layer_id, whose value is the stable "layer:<slug>" the layer page is keyed
  // by. Reconstructing an id from the name never matched once the enrichment
  // pass had renamed a layer.
  const layerName =
    typeof page.metadata?.layer_name === "string" ? page.metadata.layer_name : "";
  const layerId =
    typeof page.metadata?.layer_id === "string" ? page.metadata.layer_id : "";
  const layerPage = useMemo(
    () =>
      layerId
        ? pages.find((p) => p.page_type === "layer_page" && p.target_path === layerId)
        : undefined,
    [pages, layerId],
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
          {/* Centred in the space the rail leaves, with the rail itself flush
              to the edge. The two alternatives both fail on a wide window:
              anchoring the column left opens a ~620px hole between it and the
              rail, and centring the column-plus-rail group as a unit unpins the
              rail from the edge and strands whitespace to its right. Centring
              here makes the gap to the rail equal the gap to the tree, so both
              read as margins rather than as a gap. */}
          <div className="mx-auto w-full max-w-[720px] px-4 py-8 sm:px-6">
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

            {/* One quiet provenance line: what kind of page this is, who wrote
                it, and when. Previously this row carried an accent-filled
                "Regenerate" pill immediately beside the h1, which read as a
                statement about the page rather than an action on it and
                out-shouted the title. The upgrade affordance now sits at the
                end of the content, where a reader has seen the page is thin.

                "Written by <model>" / "Built from the index" is the Overview
                page's vocabulary, deliberately: a page can lack prose because
                its provider call failed, so calling that "deterministic" would
                be untrue. */}
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[11px] text-[var(--color-text-tertiary)] mb-5">
              <span className="font-mono text-[10px] uppercase tracking-[0.12em]">
                {getPageLabel(page)}
              </span>
              <span aria-hidden className="opacity-40">&middot;</span>
              <span className="text-[var(--color-text-secondary)]">
                {isTemplatePage
                  ? "Built from the index"
                  : page.model_name
                    ? `Written by ${page.model_name}`
                    : "Written by a model"}
              </span>
              <span aria-hidden className="opacity-40">&middot;</span>
              <span title={page.updated_at}>
                updated {formatRelativeTime(page.updated_at)}
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
            </div>

            {/* What this page was written from. The rail carried this as
                basenames only, below the fold of a narrow column; at the top of
                the page it frames everything under it, and it is the one piece
                of provenance a reader wants *before* reading rather than
                after. Collapsed by default — it answers a question, it does not
                raise one. */}
            {sources.length > 0 && (
              <details className="group mb-6 rounded-lg border border-[var(--color-border-default)]">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-3.5 py-2 text-xs text-[var(--color-text-secondary)]">
                  <ChevronRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)] transition-transform group-open:rotate-90" />
                  <span>
                    Built from {sources.length} source{sources.length === 1 ? "" : " files"}
                  </span>
                  <span className="ml-auto truncate font-mono text-[10px] text-[var(--color-text-tertiary)]">
                    {sources
                      .slice(0, 3)
                      .map((s) => s.path.split("/").pop())
                      .join(", ")}
                    {sources.length > 3 && " …"}
                  </span>
                </summary>
                <ul className="flex flex-col gap-1 border-t border-[var(--color-border-default)] px-3.5 py-2.5">
                  {sources.map((s) => (
                    <li key={s.path} className="text-xs">
                      {s.pageId ? (
                        <button
                          onClick={() => goToPageId(s.pageId!)}
                          className="block w-full truncate text-left font-mono text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-accent-primary)]"
                          title={`${s.path} (${s.kind})`}
                        >
                          {s.path}
                        </button>
                      ) : (
                        <span
                          className="block truncate font-mono text-[var(--color-text-tertiary)]"
                          title={`${s.path} (${s.kind})`}
                        >
                          {s.path}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}

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

            {/* Markdown content.
                No `prose` wrapper: every element the renderer emits is already
                styled through our own tokens, so the plugin contributed exactly
                two things — a hardcoded `prose-invert` that fed dark variables
                to light mode, and `code::before/::after { content: "`" }`, which
                printed literal backticks around every unresolved inline ref. */}
            <article className="max-w-none leading-relaxed overflow-hidden">
              <WikiMarkdown
                content={visibleContent}
                wikiLinks={wikiLinks}
                buildHref={(pid) => buildPageHref(pid)}
                LinkComponent={WikiInlineLink}
                pages={pages}
              />
            </article>

            {/* The upgrade affordance, at the end of the content rather than
                beside the title. Someone who has read to here knows the page is
                thin; someone at the title does not yet, and an accent pill up
                there competed with the h1 for a decision they could not make. */}
            {isTemplatePage && upgradeSlot && (
              <div className="mt-8 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-[var(--color-border-default)] pt-4">
                <p className="text-xs text-[var(--color-text-tertiary)]">
                  This page is built from the index. A model can write the how and
                  why on top of it.
                </p>
                {upgradeSlot}
              </div>
            )}

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

            {/* Where the rail goes when there is no room for a rail.
                Nothing is dropped at narrow widths, it relocates: the reader
                already did this for Related, and the same treatment now covers
                the intelligence sections and the contents. The breakpoint is
                2xl, not lg — see the rail below for why. */}
            <div className="mt-10 grid gap-8 border-t border-[var(--color-border-default)] pt-6 sm:grid-cols-2 2xl:hidden">
              {intelligenceSlot && (
                <div className="flex flex-col gap-4">{intelligenceSlot}</div>
              )}
              {relatedLinks.length > 0 && (
                <div>
                  <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                    Related
                  </p>
                  <ul className="flex flex-col gap-2">
                    {relatedLinks.slice(0, RELATED_LIMIT).map((r) => (
                      <li key={r.id}>
                        <button
                          onClick={() => goToPageId(r.id)}
                          className="block w-full text-left text-xs leading-snug text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-accent-primary)]"
                        >
                          {r.title}
                        </button>
                        {r.reason && (
                          <span className="block font-mono text-[10px] text-[var(--color-text-tertiary)]">
                            {RELATED_REASON_LABELS[r.reason]}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Right rail — three zones, in the order a reader wants them: where am
          I in this page, what do I need to know about this code, where do I go
          next. The receipt sits under a hairline at the bottom.

          It used to carry eight blocks behind five uppercase labels, which is
          mostly label for about a dozen rows, and the intelligence sections
          each announced themselves separately even when they were three rows
          long. At a glance, Importance, Community, Call graph and Security are
          now one Signals list assembled by the host.

          `2xl` (1536px), not `lg` (1024px): the chrome either side of the
          reading column is 56 + 288 + 300 = 644px, and body copy at 16px wants
          about 640px to reach 65 characters. At lg the column landed at ~420px,
          so the rail was switching on some 400px before the layout could pay
          for it. Below 2xl every section here renders under the article
          instead. */}
      {sidebarOpen && (
        <div className="hidden 2xl:block shrink-0 w-[300px] overflow-auto">
          <div className="flex flex-col gap-7 py-8 pl-6 pr-7">
            <TableOfContents content={bodyContent} />

            {intelligenceSlot}

            {relatedLinks.length > 0 && (
              <div>
                <p className="mb-2.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                  Related
                </p>
                <ul className="flex flex-col gap-2.5">
                  {relatedLinks.slice(0, RELATED_LIMIT).map((r) => (
                    <li key={r.id}>
                      {/* Wraps rather than truncates. At 260px the old rail
                          rendered "File: packages/server/src/repowise/s…",
                          which names nothing. */}
                      <button
                        onClick={() => goToPageId(r.id)}
                        className="block w-full text-left text-xs leading-snug text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-accent-primary)]"
                      >
                        {r.title}
                      </button>
                      {r.reason && (
                        <span className="block font-mono text-[10px] text-[var(--color-text-tertiary)]">
                          {RELATED_REASON_LABELS[r.reason]}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
                {relatedLinks.length > RELATED_LIMIT && (
                  <p className="mt-2 text-[10px] text-[var(--color-text-tertiary)]">
                    + {relatedLinks.length - RELATED_LIMIT} more
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

            {/* How this page was made. A receipt, so it sits at the bottom
                under a rule rather than above the things you came for. */}
            <div className="mt-auto flex flex-wrap gap-x-3 gap-y-1 border-t border-[var(--color-border-default)] pt-3.5 font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
              {page.model_name && (
                <span className="truncate" title={page.model_name}>
                  {page.model_name}
                </span>
              )}
              <span>
                {formatTokens(page.input_tokens)} in · {formatTokens(page.output_tokens)} out
              </span>
              <span>v{page.version}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
