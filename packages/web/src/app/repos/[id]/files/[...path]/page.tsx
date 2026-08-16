import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { bundledLanguages, getSingletonHighlighter, type BundledLanguage } from "shiki";
import { getFileContent, getFileDetail } from "@/lib/api/files";
import { WikiMarkdown } from "@repowise-dev/ui/wiki/wiki-markdown";
import { FilePageHost } from "@/components/files/file-page-host";
import { FILE_PAGE_TABS, type FilePageTab } from "@repowise-dev/ui/files";
import type { FileDetailResponse } from "@repowise-dev/types/files";

/** Matches the sibling routes (`decisions`, the repo root, the four workspace
 *  pages). Reading `searchParams` below keeps the route itself dynamic, so
 *  what this buys is the segment's default fetch cache: `apiGet` sets no
 *  `cache`/`next` options, so the aggregate and the file content are otherwise
 *  re-fetched on every render. Their inputs only move when the index is
 *  rebuilt. */
export const revalidate = 30;

interface Props {
  params: Promise<{ id: string; path: string[] }>;
  searchParams: Promise<{ tab?: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { path } = await params;
  const filePath = path.map(decodeURIComponent).join("/");
  return { title: filePath.split("/").pop() ?? filePath };
}

/** Line-decorated shiki output is held in memory for the whole request and
 *  then crosses the client boundary as a string, so the ceiling is on the
 *  rendered page rather than on the source. 100KB is ~2.5k lines of code —
 *  past that the coverage tab falls back to the summary-only view. */
const MAX_HIGHLIGHTED_BYTES = 100_000;

const THEMES = { light: "github-light", dark: "vesper" } as const;

/** Best-effort shiki render of the file with per-line coverage attributes.
 *  Returns undefined when the source can't be fetched or highlighted —
 *  the Coverage tab falls back to the summary-only view. */
async function renderCoverageCode(
  content: string | undefined,
  detail: FileDetailResponse,
): Promise<string | undefined> {
  if (!content) return undefined;
  if (!detail.coverage || detail.coverage.covered_lines.length === 0) return undefined;
  if (content.length > MAX_HIGHLIGHTED_BYTES) return undefined;

  // Resolve the grammar before highlighting rather than catching a throw and
  // running a second full pass in "text". An unknown language is a known
  // state, not an exception.
  const declared = detail.graph?.language?.toLowerCase();
  const lang =
    declared && declared in bundledLanguages ? (declared as BundledLanguage) : "text";

  const covered = new Set(detail.coverage.covered_lines);
  try {
    // Singleton: one highlighter and one grammar/theme registry for the
    // process, loaded on demand. The `codeToHtml` shortcut used to build a
    // fresh one per call off the full bundle — every language, both themes.
    const highlighter = await getSingletonHighlighter({
      themes: [THEMES.light, THEMES.dark],
      langs: [],
    });
    if (lang !== "text" && !highlighter.getLoadedLanguages().includes(lang)) {
      await highlighter.loadLanguage(lang);
    }
    return highlighter.codeToHtml(content, {
      lang,
      themes: THEMES,
      defaultColor: false,
      transformers: [
        {
          line(node, line) {
            if (covered.has(line)) node.properties["data-covered"] = "y";
          },
        },
      ],
    });
  } catch {
    return undefined;
  }
}

export default async function FileEntityPage({ params, searchParams }: Props) {
  const { id, path } = await params;
  const { tab } = await searchParams;
  const filePath = path.map(decodeURIComponent).join("/");

  const initialTab =
    tab && (FILE_PAGE_TABS as readonly string[]).includes(tab)
      ? (tab as FilePageTab)
      : undefined;

  // Only the Coverage tab renders highlighted source, and reading the file off
  // disk costs three DB queries of its own inside `/file-content`. Fetched
  // beside the aggregate rather than after it: the highlight needs `detail`,
  // but the source bytes do not.
  const wantsCoverage = initialTab === "coverage";
  let detail: FileDetailResponse;
  let source: string | undefined;
  try {
    [detail, source] = await Promise.all([
      getFileDetail(id, filePath),
      wantsCoverage
        ? getFileContent(id, filePath).catch(() => undefined)
        : Promise.resolve(undefined),
    ]);
  } catch {
    notFound();
  }

  const coverageCodeHtml = await renderCoverageCode(source, detail);

  const docSlot = detail.wiki_page ? (
    <WikiMarkdown content={detail.wiki_page.content} />
  ) : undefined;
  const wikiHref = detail.wiki_page
    ? `/repos/${id}/docs?page=${encodeURIComponent(detail.wiki_page.id)}`
    : undefined;

  // `FilePageHost` is a client component taking `data` whole, so everything in
  // it is serialized into the RSC flight payload *and* inlined into the HTML —
  // downloaded twice. These two blocks were consumed above, on the server, and
  // are read by nothing in the client tree: the wiki body became `docSlot`,
  // and the coverage line array became `coverageCodeHtml` plus a count.
  const clientData: FileDetailResponse = {
    ...detail,
    wiki_page: detail.wiki_page ? { ...detail.wiki_page, content: "" } : null,
    coverage: detail.coverage
      ? {
          ...detail.coverage,
          covered_line_count:
            detail.coverage.covered_line_count ?? detail.coverage.covered_lines.length,
          covered_lines: [],
        }
      : null,
  };

  return (
    <div className="p-4 sm:p-6 max-w-[1200px]">
      <FilePageHost
        repoId={id}
        data={clientData}
        docSlot={docSlot}
        coverageCodeHtml={coverageCodeHtml}
        wikiHref={wikiHref}
        initialTab={initialTab}
      />
    </div>
  );
}
