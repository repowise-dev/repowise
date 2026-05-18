import { MDXRemote } from "next-mdx-remote/rsc";
import { codeToHtml } from "shiki";
import { CodeBlock } from "@repowise-dev/ui/wiki/code-block";
import { MermaidDiagram } from "@repowise-dev/ui/wiki/mermaid-diagram";
import { wikiMdxComponents } from "@repowise-dev/ui/wiki/wiki-mdx-components";
import { preprocessWikiCodeBlocks } from "@repowise-dev/ui/wiki/wiki-mdx-preprocess";
import { WikiLink } from "@repowise-dev/ui/wiki/wiki-link";
import {
  buildAnchorIndex,
  type WikiLinkRef,
} from "@repowise-dev/ui/wiki/wiki-links-types";

interface Props {
  content: string;
  /** Pre-resolved wiki links from ``page.metadata.wiki_links``. */
  wikiLinks?: WikiLinkRef[];
  /** Repo ID required to build cross-page hrefs. */
  repoId?: string;
}

export async function WikiRenderer({ content, wikiLinks, repoId }: Props) {
  // Build the anchor → page_id index once per render; the preprocessor
  // does an O(1) Map lookup per backtick match.
  const index = buildAnchorIndex(wikiLinks ?? []);
  const resolveWikiLink = repoId
    ? (anchor: string) => index.get(anchor) ?? null
    : undefined;
  const buildWikiLinkUrl = (pageId: string) =>
    `/repos/${repoId}/wiki/${encodeURIComponent(pageId)}`;

  const preprocessed = await preprocessWikiCodeBlocks(content, {
    highlight: async (code, lang) =>
      codeToHtml(code, {
        lang: lang as Parameters<typeof codeToHtml>[1]["lang"],
        theme: "vesper",
      }),
    resolveWikiLink,
    buildWikiLinkUrl,
  });

  return (
    <MDXRemote
      source={preprocessed.source}
      components={{
        ...wikiMdxComponents,
        ShikiBlock: ({ html, code, lang }: { html: string; code: string; lang: string }) => (
          <CodeBlock html={html} code={code} language={lang} />
        ),
        MermaidBlock: ({ chart }: { chart: string }) => (
          <MermaidDiagram chart={chart} />
        ),
        WikiLink,
      }}
      options={{
        parseFrontmatter: false,
        mdxOptions: {
          remarkPlugins: [],
          rehypePlugins: [],
        },
      }}
    />
  );
}
