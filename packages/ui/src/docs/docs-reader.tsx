"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  FileText,
  ArrowRight,
  ArrowLeft,
  ChevronRight,
  Layers,
} from "lucide-react";
import type { DocPage, DocPageSummary } from "@repowise-dev/types/docs";
import { cn } from "../lib/cn";
import { formatTokens } from "../lib/format";
import { RelativeTime } from "../shared/relative-time";
import { getPageLabel, isStubFallbackPage } from "../lib/page-types";
import { citationKey, citationLabel, readCitations } from "./citations";
import { computeDocNav } from "./doc-nav";
import { filterMarkdownByPersona, type ReaderPersona } from "./reader-persona";
import { ModuleHistory } from "./module-history";
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
import { ViewTabs } from "../shared/view-tabs";
import { Skeleton } from "../ui/skeleton";

/** Related entries shown before the "+ N more" line. Five, not eight: the list
 *  is a suggestion of where to go next, and past about five it reads as a dump
 *  of everything the graph knows. */
const RELATED_LIMIT = 5;

export interface DocsReaderCopy {
  selectPageTitle: string;
  selectPageBody: string;
  missingTitle: string;
  /** Why the requested page is absent; `path` is the readable half of its id. */
  missingBody: (path: React.ReactNode) => React.ReactNode;
  builtFromIndex: string;
  writtenBy: (model: string) => string;
  writtenByModel: string;
  updated: (relative: string) => string;
  inModule: (name: string) => string;
  openFilePage: string;
  openFilePageTitle: (path: string) => string;
  builtFromSources: (count: number) => string;
  viewPage: string;
  viewReference: string;
  viewsLabel: string;
  referenceIntro: string;
  proseLost: string;
  proseNotWritten: string;
  previous: string;
  next: string;
  possibleInaccuracies: string;
  related: string;
  relatedReasons: Record<RelatedReason, string>;
  moreRelated: (count: number) => string;
}

export const DEFAULT_DOCS_READER_COPY: DocsReaderCopy = {
  selectPageTitle: "Select a page",
  selectPageBody: "Choose a file or module from the tree to read what Repowise wrote about it.",
  missingTitle: "No page for this one yet",
  missingBody: (path) => (
    <>
      Repowise has not written a page for {path}. It documents the files that
      carry the repository&apos;s shape first. Pick another from the tree, or
      re-run the index with a wider page budget.
    </>
  ),
  builtFromIndex: "Built from the index",
  writtenBy: (model) => `Written by ${model}`,
  writtenByModel: "Written by a model",
  updated: (relative) => `updated ${relative}`,
  inModule: (name) => `in ${name}`,
  openFilePage: "Open file page",
  openFilePageTitle: (path) => `Health, history and dependents for ${path}`,
  builtFromSources: (n) => `Built from ${n} source${n === 1 ? "" : " files"}`,
  viewPage: "Page",
  viewReference: "Reference",
  viewsLabel: "Page or reference",
  referenceIntro:
    "What this page carries for search and agents: the questions it answers, the identifiers behind it and its git history.",
  proseLost:
    "This page is built from the index. A model was meant to write over it on the last run and the call did not complete.",
  proseNotWritten:
    "This page is built from the index. A model can write the how and why on top of it.",
  previous: "Previous",
  next: "Next",
  possibleInaccuracies: "Possible inaccuracies detected",
  related: "Related",
  relatedReasons: {
    imports: "imports",
    "imported-by": "imported by",
    "co-changes-with": "changes together",
    "same-module": "same module",
  },
  moreRelated: (n) => `+ ${n} more`,
};

// Remove the level-1 heading a body opens with. The reader renders the page
// title as the H1 above the body, so a second H1 is the same page named twice,
// and when the writer worded it differently ("repowise: overview" under
// "Repository Overview: repowise") it reads as two pages. Only a heading on the
// first non-blank line is removed; a later H1 is content.
function stripLeadingTitleHeading(content: string): string {
  const lines = content.split("\n");
  let i = 0;
  while (i < lines.length && lines[i]!.trim() === "") i++;
  if (!/^#\s+\S/.test(lines[i] ?? "")) return content;
  lines.splice(0, i + 1);
  while (lines.length > 0 && lines[0]!.trim() === "") lines.shift();
  return lines.join("\n");
}

/** The readable half of a page id, for telling someone what they asked for.
 *  Ids are `"{page_type}:{target_path}"`, and the path is the part that names
 *  anything; a bare `file_page:` prefix on screen is machinery, not an answer. */
function describePageId(pageId: string): string {
  const colon = pageId.indexOf(":");
  const path = colon === -1 ? pageId : pageId.slice(colon + 1);
  return path.trim() || pageId;
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
  /**
   * Href for the source file's own page, given a repo-relative path.
   *
   * The reader is the one surface that knows a page documents a specific file —
   * `page.target_path` on a `file_page` is exactly the path the file route
   * takes — and until this it was the only surface with no link to it at all.
   * Optional so a host without a file route (the VS Code webview) simply
   * renders no door rather than a broken one.
   */
  buildFileHref?: ((filePath: string) => string) | undefined;
  /**
   * The page id that was asked for and could not be fetched.
   *
   * Set it only once the fetch has settled — while it is in flight the host
   * should be passing `isLoading` instead, or the reader flashes "no page for
   * this one" over a request that is about to succeed.
   */
  missingPageId?: string | undefined;
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
  /**
   * The page's own model action ("Write this page with AI" / "Rewrite with
   * AI"), rendered at the end of the metadata row under the title. Host owns
   * the launch and decides which pages can be written; omit it elsewhere.
   */
  pageActionSlot?: React.ReactNode;
  /**
   * Where the "out of date" mark in the metadata row links (the host's doc
   * drift view). Without it the mark is plain text. A fresh page shows no mark.
   */
  freshnessHref?: string | undefined;
  /** Overrides for the out-of-date wording, for a host translating it. */
  freshnessLabels?: FreshnessLabels | undefined;
  /** Overrides for the reader's other wording, for a host translating it. */
  copy?: Partial<DocsReaderCopy> | undefined;
}

export interface FreshnessLabels {
  /** `freshness_status: "stale"`: the code moved since the page was written. */
  stale?: string;
  /** `freshness_status: "outdated"`. */
  outdated?: string;
}

const DEFAULT_FRESHNESS_LABELS: Required<FreshnessLabels> = {
  stale: "May be out of date",
  outdated: "Out of date",
};

/**
 * Freshness as metadata: nothing when the page is fresh (the quiet default),
 * a warning dot and a word when it is not. Keyed on the stored status only;
 * the confidence score is not a freshness signal for pages built from the
 * index, so it is never used as a fallback here.
 */
function FreshnessMark({
  status,
  href,
  labels,
  LinkComponent,
}: {
  status: string | undefined;
  href?: string | undefined;
  labels?: FreshnessLabels | undefined;
  LinkComponent: ReaderLinkComponent;
}) {
  if (status !== "stale" && status !== "outdated") return null;
  const text = labels?.[status] ?? DEFAULT_FRESHNESS_LABELS[status];
  const inner = (
    <>
      <span
        aria-hidden
        className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-warning)]"
      />
      {text}
    </>
  );
  const cls = "inline-flex items-center gap-1.5 text-[var(--color-text-secondary)]";
  return (
    <>
      {href ? (
        <LinkComponent
          href={href}
          className={cn(cls, "hover:text-[var(--color-text-primary)] hover:underline")}
        >
          {inner}
        </LinkComponent>
      ) : (
        <span className={cls}>{inner}</span>
      )}
      <span aria-hidden className="opacity-40">&middot;</span>
    </>
  );
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
  buildFileHref,
  missingPageId,
  LinkComponent,
  intelligenceSlot,
  versionHistorySlot,
  upgradeSlot,
  pageActionSlot,
  freshnessHref,
  freshnessLabels,
  copy,
}: DocsReaderProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const c = useMemo(() => ({ ...DEFAULT_DOCS_READER_COPY, ...copy }), [copy]);

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
    // Two different states wearing one screen until now. A reader who opened
    // the surface with nothing selected needs "pick something". A reader who
    // asked for a specific page and did not get it needs to be told that, or
    // the generic prompt reads as the link having done nothing — and links
    // into a specific page arrive from all over the app (row actions, the
    // command palette, bookmarks), each of which can name a page the index
    // never wrote or has since dropped.
    const requested = missingPageId ? describePageId(missingPageId) : null;
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
        <div className="rounded-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)] p-4">
          <FileText className="h-8 w-8 text-[var(--color-text-tertiary)]" />
        </div>
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            {requested ? c.missingTitle : c.selectPageTitle}
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)] max-w-sm">
            {requested
              ? c.missingBody(
                  <span className="font-mono text-[var(--color-text-primary)] break-all">
                    {requested}
                  </span>,
                )
              : c.selectPageBody}
          </p>
        </div>
      </div>
    );
  }

  return (
    <DocsReaderBody
      // Keyed so the Page / Reference choice resets with the page.
      key={page.id}
      page={page}
      pages={pages}
      repoId={repoId}
      sidebarOpen={sidebarOpen}
      scrollRef={scrollRef}
      goToPageId={goToPageId}
      persona={persona}
      buildPageHref={buildPageHref}
      buildFileHref={buildFileHref}
      LinkComponent={LinkComponent}
      intelligenceSlot={intelligenceSlot}
      versionHistorySlot={versionHistorySlot}
      upgradeSlot={upgradeSlot}
      pageActionSlot={pageActionSlot}
      freshnessHref={freshnessHref}
      freshnessLabels={freshnessLabels}
      c={c}
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
  buildFileHref,
  LinkComponent,
  intelligenceSlot,
  versionHistorySlot,
  upgradeSlot,
  pageActionSlot,
  freshnessHref,
  freshnessLabels,
  c,
}: {
  page: DocPage;
  pages: DocPageSummary[];
  repoId: string;
  sidebarOpen: boolean;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  goToPageId: (pageId: string) => void;
  persona: ReaderPersona;
  buildPageHref: (pageId: string) => string;
  buildFileHref?: ((filePath: string) => string) | undefined;
  LinkComponent: ReaderLinkComponent;
  intelligenceSlot?: React.ReactNode;
  versionHistorySlot?: React.ReactNode;
  upgradeSlot?: React.ReactNode;
  pageActionSlot?: React.ReactNode;
  freshnessHref?: string | undefined;
  freshnessLabels?: FreshnessLabels | undefined;
  c: DocsReaderCopy;
}) {
  const nav = useMemo(() => computeDocNav(page, pages), [page, pages]);
  const wikiLinks = useMemo(() => getWikiLinks(page.metadata), [page.metadata]);

  // A page with no model behind it. Structural pages are templates by design
  // and always will be; a model-written page that is still a template has
  // prose outstanding, which is what the upgrade affordance is for.
  const isTemplatePage = page.provider_name === "template";

  // Of those, the ones where prose was attempted and lost rather than never
  // asked for. This is the only per-page trust caveat the reader still draws,
  // and it rides along with the affordance that fixes it instead of getting a
  // callout of its own. See the end-of-content block below.
  const proseLost = isStubFallbackPage(page);

  // The reader renders the page title as the H1 above the body, but generated
  // content often opens with its own "# <title>" line (the deterministic
  // templates do), so the page is named twice. Drop the body's opening H1
  // before anything else reads the content.
  const bodyContent = useMemo(
    () => stripLeadingTitleHeading(page.content),
    [page.content],
  );

  const visibleContent = useMemo(
    () => filterMarkdownByPersona(bodyContent, persona),
    [bodyContent, persona],
  );

  // The agent digest: questions, identifiers, public API and git signals the
  // page carries for search and MCP, kept off the body and shown on request.
  const digest = page.digest?.trim() ?? "";
  const [view, setView] = useState<"page" | "reference">("page");
  const showingReference = view === "reference" && digest !== "";

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
  // The source file this page documents, when there is one. `target_path` on a
  // `file_page` is the repo-relative path the file route takes — the same pair
  // the file endpoint matches on — so no id parsing is involved.
  const sourceFileHref =
    page.page_type === "file_page" && page.target_path && buildFileHref
      ? buildFileHref(page.target_path)
      : undefined;

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
    const byPath = new Map(pages.map((p) => [p.target_path, p.id]));
    return readCitations(page.metadata?.sources, (path) => byPath.get(path));
  }, [page.metadata, pages]);
  // The commit the cited lines were read at; spans link to the forge at it.
  const sourcesCommit =
    typeof page.metadata?.sources_commit === "string" ? page.metadata.sources_commit.slice(0, 7) : "";

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

  /* No `prose` wrapper: every element the renderer emits is already styled
     through our own tokens, so the plugin contributed exactly two things, a
     hardcoded `prose-invert` that fed dark variables to light mode and
     `code::before/::after { content: "`" }`, which printed literal backticks
     around every unresolved inline ref. */
  const article = (markdown: string) => (
    <article data-chat-selection="" className="max-w-none leading-relaxed overflow-hidden">
      <WikiMarkdown
        content={markdown}
        wikiLinks={wikiLinks}
        buildHref={(pid) => buildPageHref(pid)}
        LinkComponent={WikiInlineLink}
        pages={pages}
      />
    </article>
  );

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
                it, when, and whether it fell behind its code. A host's page
                action (`pageActionSlot`) ends the row as plain model-coloured
                text, so it never competes with the title.

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
                  ? c.builtFromIndex
                  : page.model_name
                    ? c.writtenBy(page.model_name)
                    : c.writtenByModel}
              </span>
              <span aria-hidden className="opacity-40">&middot;</span>
              <FreshnessMark
                status={page.freshness_status}
                href={freshnessHref}
                labels={freshnessLabels}
                LinkComponent={LinkComponent}
              />
              <span title={page.updated_at}>
                <RelativeTime date={page.updated_at} template={c.updated("{time}")} />
              </span>
              {moduleSeg && (
                <button
                  onClick={() => goToPageId(moduleSeg.pageId!)}
                  className="rounded-full border border-[var(--color-border-default)] px-2 py-0.5 text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-accent)] hover:text-[var(--color-accent-primary)]"
                >
                  {c.inModule(moduleSeg.label)}
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

              {/* The door out to the code this page is about. The docs surface
                  rendered no link to a file page anywhere, on any of its
                  screens, so a reader who finished a file's documentation and
                  wanted its health, history or dependents had to go back to
                  the tree and start again from Files.

                  It sits at the end of the provenance row rather than in the
                  rail, because the rail is `2xl`-only and this is the one link
                  on the page that has to survive a laptop.

                  Two things it does not inherit from that row. It sets its own
                  `text-xs`, because the row runs at 11px for metadata and this
                  is the page's only action — an action smaller than every body
                  size on the surface is not one. And it does not take
                  `ml-auto`: this row wraps (a long model name beside a module
                  pill and a layer pill does it at laptop width), and a flex
                  item only right-aligns while it still shares a line, so
                  `ml-auto` bought a tidy right edge on wide windows and a link
                  stranded alone against the far margin on the widths that
                  actually wrap. Inline, after the pills, it reads the same at
                  every width. */}
              {sourceFileHref && (
                <LinkComponent
                  href={sourceFileHref}
                  className="shrink-0 text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
                  title={c.openFilePageTitle(page.target_path)}
                >
                  {c.openFilePage} <span aria-hidden>&rarr;</span>
                </LinkComponent>
              )}
              {pageActionSlot}
            </div>

            {/* What this page was written from. The rail carried this as
                basenames only, below the fold of a narrow column; at the top of
                the page it frames everything under it, and it is the one piece
                of provenance a reader wants *before* reading rather than
                after. Collapsed by default — it answers a question, it does not
                raise one. */}
            {sources.length > 0 && (
              <details className="group/toc mb-6 rounded-lg border border-[var(--color-border-default)]">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-3.5 py-2 text-xs text-[var(--color-text-secondary)]">
                  <ChevronRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)] transition-transform group-open/toc:rotate-90" />
                  <span>
                    {c.builtFromSources(sources.length)}
                  </span>
                  <span className="ml-auto truncate font-mono text-[10px] text-[var(--color-text-tertiary)]">
                    {sources
                      .slice(0, 3)
                      .map((s) => citationLabel({ ...s, path: s.path.split("/").pop() ?? s.path }))
                      .join(", ")}
                    {sources.length > 3 && " …"}
                  </span>
                </summary>
                <ul className="flex flex-col gap-1 border-t border-[var(--color-border-default)] px-3.5 py-2.5">
                  {sources.map((s) => {
                    const label = citationLabel(s);
                    const title = `${label}${s.symbol ? ` ${s.symbol}` : ""} (${s.kind})${sourcesCommit ? ` @ ${sourcesCommit}` : ""}`;
                    const linkClass =
                      "block w-full truncate text-left font-mono text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-accent-primary)]";
                    const fileHref = buildFileHref?.(s.path);
                    return (
                      <li key={citationKey(s)} className="text-xs">
                        {s.pageId && !s.lines ? (
                          <button onClick={() => goToPageId(s.pageId!)} className={linkClass} title={title}>
                            {label}
                          </button>
                        ) : s.url ? (
                          <a href={s.url} target="_blank" rel="noopener noreferrer" className={linkClass} title={title}>
                            {label}
                          </a>
                        ) : fileHref ? (
                          <LinkComponent href={fileHref} className={linkClass} title={title}>
                            {label}
                          </LinkComponent>
                        ) : (
                          <span
                            className="block truncate font-mono text-[var(--color-text-tertiary)]"
                            title={title}
                          >
                            {label}
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </details>
            )}

            {/* Human notes (read-only callout; editing lives in the rail) */}
            {page.human_notes && (
              <div className="mb-4 rounded-lg border border-[var(--color-border-accent)] bg-[var(--color-accent-blue)]/5 px-4 py-3">
                <p className="text-sm text-[var(--color-text-secondary)] whitespace-pre-wrap leading-relaxed">
                  {page.human_notes}
                </p>
              </div>
            )}

            {/* Markdown content. A page with an agent digest gets a Page /
                Reference switch: the body is what a reader came for, the
                digest is what search and agents read, one click away. */}
            {digest ? (
              <ViewTabs
                tabs={[
                  { id: "page", label: c.viewPage },
                  { id: "reference", label: c.viewReference },
                ]}
                value={view}
                onValueChange={(id) => setView(id === "reference" ? "reference" : "page")}
                aria-label={c.viewsLabel}
              >
                {showingReference && (
                  <p className="mb-4 text-xs text-[var(--color-text-tertiary)]">
                    {c.referenceIntro}
                  </p>
                )}
                {article(showingReference ? digest : visibleContent)}
              </ViewTabs>
            ) : (
              article(visibleContent)
            )}

            {/* The upgrade affordance, at the end of the content rather than
                beside the title. Someone who has read to here knows the page is
                thin; someone at the title does not yet, and an accent pill up
                there competed with the h1 for a decision they could not make.

                The prose-lost caveat rides in the same block rather than in a
                banner of its own. It used to be a bordered warning callout
                above the content keyed on `confidence < 0.5`, which generation
                stamped on every deterministic page, so an index built without
                a key opened every page it had under "verify against the source
                before relying on it", about pages that are pure index output
                and have nothing to verify. Same sentence, same place as the
                action that resolves it, no alarm colour: the reader is told
                once, at the point it is actionable.

                `proseLost` is checked on its own rather than under
                `isTemplatePage`. A failed page is always a template here,
                because the fallback substitutes a template render, so the two
                are equivalent today. Coupling them would mean any other writer
                of the marker could stamp a real provider name and silently
                lose the caveat. */}
            {(proseLost || (isTemplatePage && upgradeSlot)) && (
              <div className="mt-8 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-[var(--color-border-default)] pt-4">
                <p className="text-xs text-[var(--color-text-tertiary)]">
                  {proseLost ? c.proseLost : c.proseNotWritten}
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
                        {c.previous}
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
                        {c.next}
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
                    {c.possibleInaccuracies}
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
              <ModuleHistory metadata={page.metadata} />
              {intelligenceSlot && (
                <div className="flex flex-col gap-4">{intelligenceSlot}</div>
              )}
              <RelatedList links={relatedLinks} goToPageId={goToPageId} c={c} />
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
            <TableOfContents content={showingReference ? digest : bodyContent} />

            <ModuleHistory metadata={page.metadata} />

            {intelligenceSlot}

            <RelatedList links={relatedLinks} goToPageId={goToPageId} c={c} />

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

/**
 * Where to go next: the first few related pages, each with why it is related.
 * One list for the rail and for its narrow-width place under the article.
 */
function RelatedList({
  links,
  goToPageId,
  c,
}: {
  links: { id: string; title: string; reason?: RelatedReason }[];
  goToPageId: (pageId: string) => void;
  c: DocsReaderCopy;
}) {
  if (links.length === 0) return null;
  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {c.related}
      </p>
      <ul className="flex flex-col gap-2.5">
        {links.slice(0, RELATED_LIMIT).map((r) => (
          <li key={r.id}>
            {/* Wraps rather than truncates. At 260px the old rail rendered
                "File: packages/server/src/repowise/s…", which names nothing. */}
            <button
              onClick={() => goToPageId(r.id)}
              className="block w-full text-left text-xs leading-snug text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-accent-primary)]"
            >
              {r.title}
            </button>
            {r.reason && (
              <span className="block font-mono text-[10px] text-[var(--color-text-tertiary)]">
                {c.relatedReasons[r.reason]}
              </span>
            )}
          </li>
        ))}
      </ul>
      {links.length > RELATED_LIMIT && (
        <p className="mt-2 text-[10px] text-[var(--color-text-tertiary)]">
          {c.moreRelated(links.length - RELATED_LIMIT)}
        </p>
      )}
    </div>
  );
}
