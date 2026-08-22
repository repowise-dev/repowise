import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { bundledLanguages, getSingletonHighlighter, type BundledLanguage } from "shiki";
import { getFileContent, getFileDetail } from "@/lib/api/files";
import { WikiMarkdown } from "@repowise-dev/ui/wiki/wiki-markdown";
import {
  docsPagePath,
  fileEntityPath,
  symbolEntityPath,
} from "@repowise-dev/ui/shared/entity";
import {
  asFilePageTab,
  buildFilePanels,
  fileTabsFor,
  FilePageHeader,
  resolveFileTab,
  type FilePageTab,
} from "@repowise-dev/ui/files";
import { FilePageHost } from "@/components/files/file-page-host";
import { FileTestsPanel } from "@/components/files/file-tests-panel";
import { FileHealthPanel } from "@/components/files/file-health-panel";
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

/**
 * The three tabs whose bodies read one of the four unbounded blocks the
 * aggregate can drop (`fields=slim`): the wiki body, the covered-line array,
 * per-function blame and per-finding `details`. Landing on any other tab does
 * not need them, and the four together are most of the payload.
 */
const HEAVY_TABS: readonly FilePageTab[] = ["doc", "health", "coverage"];

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
  const prefix = `/repos/${id}`;

  const requested = asFilePageTab(tab);
  // Only a landing on one of the heavy tabs pays for the heavy blocks. The
  // aggregate's `fields=slim` skips the blame query outright and empties the
  // other three, and the host round-trips on the first click into a tab this
  // render could not produce a body for.
  const wantsFull = requested !== undefined && HEAVY_TABS.includes(requested);

  let detail: FileDetailResponse;
  let source: string | undefined;
  try {
    // Only the Coverage tab renders highlighted source, and reading the file
    // off disk costs three DB queries of its own inside `/file-content`.
    // Fetched beside the aggregate rather than after it: the highlight needs
    // `detail`, but the source bytes do not.
    [detail, source] = await Promise.all([
      getFileDetail(id, filePath, { fields: wantsFull ? "full" : "slim" }),
      requested === "coverage"
        ? getFileContent(id, filePath).catch(() => undefined)
        : Promise.resolve(undefined),
    ]);
  } catch {
    notFound();
  }

  const coverageCodeHtml = await renderCoverageCode(source, detail);

  const tabs = fileTabsFor(detail);
  const initialTab = resolveFileTab(requested, tabs);

  // Which tabs this render could not produce a usable body for. A tab with
  // nothing behind it is left out: clicking Documentation on a file that has
  // no page would otherwise buy a round trip to be shown the same empty state.
  const refetchTabs: FilePageTab[] = [];
  if (!wantsFull) {
    if (detail.wiki_page) refetchTabs.push("doc");
    // `function_blame_count` is why this reads the count rather than the array:
    // slim drops the rows, so an empty array and a dropped one are the same
    // shape on the wire, and guessing either way is wrong for some file.
    const blame = detail.function_blame_count ?? detail.function_blame.length;
    if (detail.health.metric || detail.health.findings.length > 0 || blame > 0) {
      refetchTabs.push("health");
    }
  }
  const covered = detail.coverage;
  const highlightPossible =
    !!covered && (covered.covered_line_count ?? covered.covered_lines.length) > 0;
  // Gated on not already being here. Land on `?tab=coverage` for a file over
  // the highlight size cap and `coverageCodeHtml` is undefined for good — so
  // without this, every click on the tab you are already on re-ran the whole
  // aggregate to produce byte-identical output, which is the Phase 1 bug.
  if (highlightPossible && !coverageCodeHtml && requested !== "coverage") {
    refetchTabs.push("coverage");
  }

  // Only when a page actually exists. The docs reader answers a `?page=` it
  // cannot find by naming the page that is missing, which is the right reply
  // to a reader who typed it and the wrong one to a link this page offered:
  // we already know here whether there is anything to open.
  const wikiHref = detail.wiki_page
    ? docsPagePath(prefix, detail.wiki_page.id)
    : undefined;

  const panels = buildFilePanels({
    data: detail,
    linkPrefix: prefix,
    fileHref: (p) => fileEntityPath(prefix, p),
    symbolHref: (s) => symbolEntityPath(prefix, s),
    ...(detail.wiki_page?.content
      ? { docSlot: <WikiMarkdown content={detail.wiki_page.content} /> }
      : {}),
    coverageCodeHtml,
    healthPanel: (
      <FileHealthPanel
        repoId={id}
        health={detail.health}
        functionBlame={detail.function_blame}
      />
    ),
    testsPanel: <FileTestsPanel repoId={id} filePath={detail.file_path} />,
    LinkComponent: Link,
  });

  return (
    // The Overview's wrapper verbatim: centred, `--page-pad`, 1280. The page
    // was `max-w-[1200px] p-4 sm:p-6` and left-anchored, so at 2048 it left a
    // 770px hole down the right while every ported sibling centred.
    <div className="mx-auto flex w-full max-w-[1280px] flex-col p-[var(--page-pad)]">
      <FilePageHost
        // Keyed on the path so a soft navigation between two file pages
        // remounts the shell. Without it React preserves the tab state at the
        // same tree position and the active tab strands out of sync with the
        // URL the new page was rendered for.
        key={detail.file_path}
        header={
          <FilePageHeader
            data={detail}
            linkPrefix={prefix}
            wikiHref={wikiHref}
            LinkComponent={Link}
          />
        }
        tabs={tabs}
        panels={panels}
        initialTab={initialTab}
        refetchTabs={refetchTabs}
      />
    </div>
  );
}
