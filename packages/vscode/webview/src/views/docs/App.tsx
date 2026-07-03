import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  BookOpen,
  FileCode,
  Loader2,
  PanelLeft,
  PanelLeftClose,
} from "lucide-react";
import type { DocPage } from "@repowise-dev/types/docs";
import { DocsTree } from "@repowise-dev/ui/docs/docs-tree";
import { DocsReader, type ReaderLinkComponent } from "@repowise-dev/ui/docs/docs-reader";
import { DEFAULT_PERSONA } from "@repowise-dev/ui/docs/reader-persona";
import type { ViewProps } from "../../runtime/mount";
import type { WebviewHost } from "../../runtime/rpc";

/**
 * Docs browser panel: a page tree on the left and the shared wiki reader on
 * the right. Pages ship with their markdown inline on `pagesList()`, so the
 * tree data and the reading pane draw from one fetch; only initial file->page
 * resolution needs a second call.
 */
export function App({ host, repo, params, refreshToken }: ViewProps<"docs">) {
  const [pages, setPages] = useState<DocPage[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [treeOpen, setTreeOpen] = useState(true);
  // Initial page is resolved once per mount; a refresh refetch must not yank
  // the reader back to the overview while the user is mid-read.
  const resolvedRef = useRef(false);

  const resolveInitial = useCallback(
    async (docs: DocPage[]): Promise<string | null> => {
      if (params.pageId) return params.pageId;
      if (params.filePath) {
        const byPath = docs.find((p) => p.target_path === params.filePath);
        if (byPath) return byPath.id;
        try {
          const detail = await host.api.fileDetail(params.filePath);
          if (detail.wiki_page) return detail.wiki_page.id;
        } catch {
          // No page for this file; land on the overview instead.
        }
      }
      const overview = docs.find((p) => p.page_type === "repo_overview");
      return overview?.id ?? docs[0]?.id ?? null;
    },
    [host, params.pageId, params.filePath],
  );

  useEffect(() => {
    let cancelled = false;
    setError(null);
    host.api
      .pagesList()
      .then(async (list) => {
        if (cancelled) return;
        const docs = list as unknown as DocPage[];
        setPages(docs);
        if (resolvedRef.current) return;
        resolvedRef.current = true;
        const initial = await resolveInitial(docs);
        if (!cancelled) setSelectedId(initial);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [host, refreshToken, resolveInitial]);

  const selectedPage = useMemo(
    () => (pages && selectedId ? pages.find((p) => p.id === selectedId) ?? null : null),
    [pages, selectedId],
  );

  const navigate = useCallback((pageId: string) => setSelectedId(pageId), []);

  // Backlink anchors carry ``?page=`` hrefs; intercept plain clicks for
  // in-panel nav and route real URLs to the editor's external handler.
  const LinkComponent = useMemo<ReaderLinkComponent>(
    () => makeLink(host, navigate),
    [host, navigate],
  );

  const buildPageHref = useCallback(
    (pageId: string) => `?page=${encodeURIComponent(pageId)}`,
    [],
  );

  // Markdown external links render as bare anchors (target=_blank) that the
  // reader does not own; hand http(s) clicks to the editor so they open in the
  // system browser rather than trying to navigate the webview.
  const onPaneClickCapture = useCallback(
    (event: React.MouseEvent) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      const anchor = (event.target as HTMLElement).closest("a");
      const href = anchor?.getAttribute("href");
      if (href && /^https?:\/\//i.test(href)) {
        event.preventDefault();
        host.openExternal(href);
      }
    },
    [host],
  );

  if (error) {
    return (
      <CenteredState
        icon={<AlertCircle className="h-8 w-8 text-[var(--color-error)]" />}
        title="Could not load docs"
        detail={error}
      />
    );
  }

  if (!pages) {
    return (
      <CenteredState
        icon={<Loader2 className="h-6 w-6 animate-spin text-[var(--color-accent-primary)]" />}
        title="Loading docs…"
      />
    );
  }

  if (pages.length === 0) {
    return (
      <CenteredState
        icon={<BookOpen className="h-8 w-8 text-[var(--color-text-tertiary)]" />}
        title="No documentation yet"
        detail="Run a Repowise generation for this repository to browse its docs here."
      />
    );
  }

  const sourcePath =
    selectedPage?.page_type === "file_page" && selectedPage.target_path
      ? selectedPage.target_path
      : null;

  return (
    <div className="flex h-full bg-[var(--color-bg-root)]">
      {treeOpen && (
        <aside className="w-[280px] shrink-0 border-r border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
          <DocsTree
            pages={pages}
            selectedPageId={selectedId}
            onSelectPage={(page) => setSelectedId(page.id)}
          />
        </aside>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-[var(--color-border-default)] px-3 py-2">
          <button
            onClick={() => setTreeOpen((open) => !open)}
            aria-label={treeOpen ? "Hide pages" : "Show pages"}
            aria-expanded={treeOpen}
            className="rounded-md p-1 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
          >
            {treeOpen ? (
              <PanelLeftClose className="h-4 w-4" />
            ) : (
              <PanelLeft className="h-4 w-4" />
            )}
          </button>
          <span className="truncate text-xs font-medium text-[var(--color-text-secondary)]">
            {repo.name}
          </span>
          <div className="flex-1" />
          {sourcePath && (
            <button
              onClick={() => host.openFile(sourcePath)}
              className="flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-accent)] hover:text-[var(--color-accent-primary)]"
            >
              <FileCode className="h-3.5 w-3.5" />
              Open source file
            </button>
          )}
        </div>

        <div className="min-h-0 flex-1" onClickCapture={onPaneClickCapture}>
          <DocsReader
            page={selectedPage}
            pages={pages}
            repoId={repo.id}
            onSelectPage={(page) => setSelectedId(page.id)}
            onNavigatePageId={navigate}
            persona={DEFAULT_PERSONA}
            sidebarOpen
            buildPageHref={buildPageHref}
            LinkComponent={LinkComponent}
          />
        </div>
      </div>
    </div>
  );
}

/** Anchor that keeps in-panel navigation local and defers real URLs to the host. */
function makeLink(host: WebviewHost, navigate: (pageId: string) => void): ReaderLinkComponent {
  return function PanelLink({
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
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
          if (/^https?:\/\//i.test(href)) {
            event.preventDefault();
            host.openExternal(href);
            return;
          }
          try {
            const pid = new URL(href, window.location.href).searchParams.get("page");
            if (pid) {
              event.preventDefault();
              navigate(pid);
            }
          } catch {
            // Malformed href: fall through to default navigation.
          }
        }}
      >
        {children}
      </a>
    );
  };
}

function CenteredState({
  icon,
  title,
  detail,
}: {
  icon: React.ReactNode;
  title: string;
  detail?: string;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
      {icon}
      <p className="text-sm font-semibold text-[var(--color-text-primary)]">{title}</p>
      {detail && (
        <p className="max-w-sm text-xs text-[var(--color-text-secondary)]">{detail}</p>
      )}
    </div>
  );
}
