/**
 * Docs panel: the web docs explorer composed from the same shared pieces (the
 * pages tree, the header bar, the search palette, the reader with its reader
 * level, and Present), over the same data shape: page summaries for the tree
 * and the palette, and one page fetched by id for the reader.
 *
 * The explorer's model writes, exports, chat and hosted nudges are left out:
 * the panel writes nothing with a model, and a webview cannot download. A
 * file reference opens the file in an editor; a wiki link switches the page.
 */

import { useCallback, useLayoutEffect, useMemo, useState } from "react";
import { BookOpen, PanelLeft, PanelLeftClose, Presentation, Search } from "lucide-react";
import { ApiError } from "@repowise-dev/ui/shared";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { cn } from "@repowise-dev/ui/lib/cn";
import type { DocPageSummary } from "@repowise-dev/types/docs";
import { DocsTree } from "@repowise-dev/ui/docs/docs-tree";
import { DocsCommandPalette } from "@repowise-dev/ui/docs/command-palette";
import { DocsHeader, type DocsHeaderTab } from "@repowise-dev/ui/docs/docs-header";
import { DocsMoreMenu, type DocsMenuGroup } from "@repowise-dev/ui/docs/docs-more-menu";
import { DocsReader, type ReaderLinkComponent } from "@repowise-dev/ui/docs/docs-reader";
import { ReaderLevelControl, SidebarToggle } from "@repowise-dev/ui/docs/docs-page-actions";
import {
  DEFAULT_PERSONA,
  READER_PERSONAS,
  personaFilteringApplies,
  type ReaderPersona,
} from "@repowise-dev/ui/docs/reader-persona";
import { PresentOverlay } from "@repowise-dev/ui/present";
import type { ViewProps } from "../../runtime/mount";
import type { WebviewHost } from "../../runtime/rpc";
import { fileHref, useFileLinks } from "../../runtime/fileLinks";
import { useDocsData, useDocsSearch, usePresent } from "./useDocsData";

/** The web explorer's English strings (`docs.explorer.*`, `views.docsTabs.*`, `common.search`). */
const COPY = {
  emptyTitle: "No documentation yet",
  emptyBody:
    "No pages yet. Indexing this repo builds docs from the code. To have a model write the how and why, add an API key, sign in to Claude Code, Codex or OpenCode, or run a local model.",
  hideTree: "Hide pages tree",
  showTree: "Show pages tree",
  more: "More",
  present: "Present",
  readerLevel: "Reader level",
  explorer: "Explorer",
  search: "Search",
} as const;

/** The panel has one docs view, so the switch carries the one tab. */
const TABS: DocsHeaderTab[] = [{ label: COPY.explorer, href: "?view=explorer", isActive: true, icon: "explorer" }];

const PAGE_QUERY = "?page=";
const MD = "(min-width: 768px)";

function matches(query: string): boolean {
  return window.matchMedia?.(query).matches ?? true;
}

/** Keyed on the repository, so a reconnect to another one opens fresh. */
export function App(props: ViewProps<"docs">) {
  return <DocsPanel key={props.repo.id} {...props} />;
}

function DocsPanel({ host, params, repo, refreshToken }: ViewProps<"docs">) {
  useFileLinks(host);
  const data = useDocsData(host, params, refreshToken);
  const { pages, selectedPage, setSelectedPageId } = data;
  const present = usePresent(host, pages);
  const searchFn = useDocsSearch(host, pages);
  const [persona, setPersona] = useState<ReaderPersona>(DEFAULT_PERSONA);
  const [searchOpen, setSearchOpen] = useState(false);
  // Both panels default to what the width affords, as on the web: the tree
  // from md, the rail from the 2xl breakpoint the reader places it at.
  const [treePanelOpen, setTreePanelOpen] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  useLayoutEffect(() => {
    setTreePanelOpen(matches(MD));
    setSidebarOpen(matches("(min-width: 1536px)"));
  }, []);

  const selectPage = useCallback((page: DocPageSummary) => setSelectedPageId(page.id), [setSelectedPageId]);
  const selectPageFromTree = useCallback(
    (page: DocPageSummary) => {
      setSelectedPageId(page.id);
      if (!matches(MD)) setTreePanelOpen(false);
    },
    [setSelectedPageId],
  );
  const LinkComponent = useMemo(() => makeLink(host, setSelectedPageId), [host, setSelectedPageId]);
  const personaHasEffect = useMemo(
    () => (selectedPage ? personaFilteringApplies(selectedPage.content) : false),
    [selectedPage],
  );

  return (
    <div className="relative flex h-full bg-[var(--color-bg-root)]">
      <TreePanel
        open={treePanelOpen}
        pages={pages}
        error={data.listError}
        selectedPageId={data.selectedPageId}
        onSelectPage={selectPageFromTree}
      />
      <TreeToggle open={treePanelOpen} onToggle={() => setTreePanelOpen((o) => !o)} />

      <div className={cn("flex min-w-0 flex-1 flex-col", !treePanelOpen && "pl-6")}>
        <DocsHeader tabs={TABS} LinkComponent={LinkComponent}>
          {personaHasEffect && (
            <ReaderLevelControl persona={persona} setPersona={setPersona} className="hidden sm:inline-flex" />
          )}
          <SearchButton onClick={() => setSearchOpen(true)} />
          {selectedPage && <SidebarToggle open={sidebarOpen} onToggle={() => setSidebarOpen((o) => !o)} />}
          <DocsMoreMenu
            label={COPY.more}
            groups={menuGroups({
              onPresent: present.presentable ? () => void present.open() : null,
              reader: personaHasEffect ? { persona, setPersona } : null,
            })}
          />
        </DocsHeader>
        {present.error && (
          <p role="alert" className="mx-3 mt-2 text-xs text-[var(--color-text-secondary)] sm:mx-6">
            Couldn&apos;t open Present: {present.error}
          </p>
        )}

        <ReaderPane
          host={host}
          repoId={repo.id}
          data={data}
          persona={persona}
          sidebarOpen={sidebarOpen}
          LinkComponent={LinkComponent}
          onSelectPage={selectPage}
        />
      </div>

      {present.model && (
        <PresentOverlay
          model={present.model}
          onClose={present.close}
          onOpenPage={(pageId) => {
            setSelectedPageId(pageId);
            present.close();
          }}
        />
      )}

      {pages && (
        <DocsCommandPalette
          pages={pages}
          open={searchOpen}
          onOpenChange={setSearchOpen}
          onSelect={selectPage}
          searchFn={searchFn}
        />
      )}
    </div>
  );
}

/** The More menu: Present, and the reader level where the bar hides it. */
function menuGroups({
  onPresent,
  reader,
}: {
  onPresent: (() => void) | null;
  reader: { persona: ReaderPersona; setPersona: (next: ReaderPersona) => void } | null;
}): DocsMenuGroup[] {
  return [
    {
      key: "present",
      items: onPresent ? [{ key: "present", label: COPY.present, icon: Presentation, onSelect: onPresent }] : [],
    },
    {
      key: "reader",
      label: COPY.readerLevel,
      phoneOnly: true,
      items: reader
        ? READER_PERSONAS.map((p) => ({
            key: `reader-${p.value}`,
            label: p.label,
            checked: reader.persona === p.value,
            onSelect: () => reader.setPersona(p.value),
          }))
        : [],
    },
  ];
}

/**
 * The pages tree. Below md it is a drawer over the reader, as on the web. A
 * failed listing costs the tree, not the reader.
 */
function TreePanel({
  open,
  pages,
  error,
  selectedPageId,
  onSelectPage,
}: {
  open: boolean;
  pages: DocPageSummary[] | null;
  error: string | null;
  selectedPageId: string | null;
  onSelectPage: (page: DocPageSummary) => void;
}) {
  let content: React.ReactNode;
  if (pages) {
    content = <DocsTree pages={pages} selectedPageId={selectedPageId} onSelectPage={onSelectPage} />;
  } else if (error) {
    content = <ApiError className="m-3" title="Could not load docs" message={error} />;
  } else {
    content = (
      <div className="space-y-2 p-3" aria-label="Loading pages">
        <Skeleton className="h-8 w-full rounded-md" />
        {["w-3/4", "w-1/2", "w-5/6", "w-2/3", "w-3/4", "w-1/2"].map((w, i) => (
          <Skeleton key={i} className={`h-4 ${w} rounded`} />
        ))}
      </div>
    );
  }
  return (
    <div
      className={cn(
        "shrink-0 overflow-y-auto bg-[var(--color-bg-surface)] transition-all duration-200",
        open ? "absolute inset-y-0 left-0 z-30 w-full md:static md:w-[288px]" : "w-0 overflow-hidden",
      )}
    >
      {content}
    </div>
  );
}

function TreeToggle({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      aria-label={open ? COPY.hideTree : COPY.showTree}
      aria-expanded={open}
      className={cn(
        "absolute top-3.5 z-40 rounded-md p-1 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
        open ? "hidden md:block left-[292px]" : "left-1",
      )}
    >
      {open ? <PanelLeftClose className="h-3.5 w-3.5" /> : <PanelLeft className="h-3.5 w-3.5" />}
    </button>
  );
}

function SearchButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-label={COPY.search}
      className="flex h-7 shrink-0 items-center gap-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-secondary)] sm:px-2.5"
    >
      <Search className="h-3.5 w-3.5" />
      <span className="hidden sm:inline">{COPY.search}</span>
      <kbd className="hidden rounded border border-[var(--color-border-default)] px-1 py-0.5 text-[10px] sm:inline">
        ⌘K
      </kbd>
    </button>
  );
}

function buildPageHref(pageId: string): string {
  return `${PAGE_QUERY}${encodeURIComponent(pageId)}`;
}

function buildFileHref(filePath: string): string {
  return fileHref(filePath);
}

/**
 * Markdown renders external links as bare anchors the reader does not own;
 * they open in the browser rather than navigating the webview.
 */
function useExternalLinks(host: WebviewHost) {
  return useCallback(
    (event: React.MouseEvent) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      const href = (event.target as HTMLElement).closest("a")?.getAttribute("href");
      if (href && /^https?:\/\//i.test(href)) {
        event.preventDefault();
        host.openExternal(href);
      }
    },
    [host],
  );
}

function NoDocs() {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 px-8 text-center">
      <div className="rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4">
        <BookOpen className="h-8 w-8 text-[var(--color-text-tertiary)]" />
      </div>
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{COPY.emptyTitle}</h3>
        <p className="max-w-sm text-xs text-[var(--color-text-secondary)]">{COPY.emptyBody}</p>
      </div>
    </div>
  );
}

/** The reading column: the shared reader, or the empty state on a repo with no pages. */
function ReaderPane({
  host,
  repoId,
  data,
  persona,
  sidebarOpen,
  LinkComponent,
  onSelectPage,
}: {
  host: WebviewHost;
  repoId: string;
  data: ReturnType<typeof useDocsData>;
  persona: ReaderPersona;
  sidebarOpen: boolean;
  LinkComponent: ReaderLinkComponent;
  onSelectPage: (page: DocPageSummary) => void;
}) {
  const { pages, selectedPageId, selectedPage, pageLoading } = data;
  const openExternalLinks = useExternalLinks(host);
  if (pages?.length === 0 && !selectedPage) return <NoDocs />;

  // Skeleton while the page is in flight, and while the list is still
  // deciding which page to open on. Missing only once the fetch settled.
  const deciding = !selectedPageId && pages === null && data.listError === null;
  const missing = selectedPageId !== null && !pageLoading && !selectedPage;
  return (
    <div className="min-h-0 min-w-0 flex-1" onClickCapture={openExternalLinks}>
      <DocsReader
        page={selectedPage}
        pages={pages ?? []}
        repoId={repoId}
        isLoading={pageLoading || deciding}
        missingPageId={missing ? selectedPageId : undefined}
        onSelectPage={onSelectPage}
        onNavigatePageId={data.setSelectedPageId}
        persona={persona}
        sidebarOpen={sidebarOpen}
        buildPageHref={buildPageHref}
        buildFileHref={buildFileHref}
        LinkComponent={LinkComponent}
      />
    </div>
  );
}

/**
 * Anchor for the reader and header: a `?page=` href switches the page in
 * place, an http(s) href opens in the browser, and file hrefs are left to the
 * document-level file-link listener.
 */
function makeLink(host: WebviewHost, openPage: (pageId: string) => void): ReaderLinkComponent {
  return function PanelLink({
    href,
    className,
    title,
    children,
    ...rest
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
        {...rest}
        onClick={(event) => {
          if (event.defaultPrevented) return;
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
          if (/^https?:\/\//i.test(href)) {
            event.preventDefault();
            host.openExternal(href);
          } else if (href.startsWith(PAGE_QUERY)) {
            event.preventDefault();
            openPage(decodeURIComponent(href.slice(PAGE_QUERY.length)));
          } else if (href.startsWith("?")) {
            // The header's own tab: there is no other view to switch to.
            event.preventDefault();
          }
        }}
      >
        {children}
      </a>
    );
  };
}
