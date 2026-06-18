import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { codeToHtml } from "shiki";
import { getFileContent, getFileDetail } from "@/lib/api/files";
import { WikiMarkdown } from "@repowise-dev/ui/wiki/wiki-markdown";
import { FilePageHost } from "@/components/files/file-page-host";
import { FILE_PAGE_TABS, type FilePageTab } from "@repowise-dev/ui/files";
import type { FileDetailResponse } from "@repowise-dev/types/files";

interface Props {
  params: Promise<{ id: string; path: string[] }>;
  searchParams: Promise<{ tab?: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { path } = await params;
  const filePath = path.map(decodeURIComponent).join("/");
  return { title: filePath.split("/").pop() ?? filePath };
}

/** Best-effort shiki render of the file with per-line coverage attributes.
 *  Returns undefined when the source can't be fetched or highlighted —
 *  the Coverage tab falls back to the summary-only view. */
async function renderCoverageCode(
  repoId: string,
  filePath: string,
  detail: FileDetailResponse,
): Promise<string | undefined> {
  if (!detail.coverage || detail.coverage.covered_lines.length === 0) return undefined;
  let content: string;
  try {
    content = await getFileContent(repoId, filePath);
  } catch {
    return undefined;
  }
  // Guard against pathological files: line-decorated shiki output for very
  // large sources costs memory on every request.
  if (content.length > 400_000) return undefined;
  const covered = new Set(detail.coverage.covered_lines);
  const lang = detail.graph?.language?.toLowerCase() || "text";
  const highlight = (language: string) =>
    codeToHtml(content, {
      lang: language as Parameters<typeof codeToHtml>[1]["lang"],
      themes: { light: "github-light", dark: "vesper" },
      defaultColor: false,
      transformers: [
        {
          line(node, line) {
            if (covered.has(line)) node.properties["data-covered"] = "y";
          },
        },
      ],
    });
  try {
    return await highlight(lang);
  } catch {
    try {
      return await highlight("text");
    } catch {
      return undefined;
    }
  }
}

export default async function FileEntityPage({ params, searchParams }: Props) {
  const { id, path } = await params;
  const { tab } = await searchParams;
  const filePath = path.map(decodeURIComponent).join("/");

  let detail: FileDetailResponse;
  try {
    detail = await getFileDetail(id, filePath);
  } catch {
    notFound();
  }

  const coverageCodeHtml = await renderCoverageCode(id, filePath, detail);
  const initialTab =
    tab && (FILE_PAGE_TABS as readonly string[]).includes(tab)
      ? (tab as FilePageTab)
      : undefined;

  const docSlot = detail.wiki_page ? (
    <WikiMarkdown content={detail.wiki_page.content} />
  ) : undefined;
  const wikiHref = detail.wiki_page
    ? `/repos/${id}/docs?page=${encodeURIComponent(detail.wiki_page.id)}`
    : undefined;

  return (
    <div className="p-4 sm:p-6 max-w-[1200px]">
      <FilePageHost
        repoId={id}
        data={detail}
        docSlot={docSlot}
        coverageCodeHtml={coverageCodeHtml}
        wikiHref={wikiHref}
        initialTab={initialTab}
      />
    </div>
  );
}
