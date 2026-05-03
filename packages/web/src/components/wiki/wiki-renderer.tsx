import { MDXRemote } from "next-mdx-remote/rsc";
import { codeToHtml } from "shiki";
import { CodeBlock } from "@repowise-dev/ui/wiki/code-block";
import { MermaidDiagram } from "@repowise-dev/ui/wiki/mermaid-diagram";
import { wikiMdxComponents } from "@repowise-dev/ui/wiki/wiki-mdx-components";
import { preprocessWikiCodeBlocks } from "@repowise-dev/ui/wiki/wiki-mdx-preprocess";

interface Props {
  content: string;
}

export async function WikiRenderer({ content }: Props) {
  const preprocessed = await preprocessWikiCodeBlocks(content, async (code, lang) =>
    codeToHtml(code, {
      lang: lang as Parameters<typeof codeToHtml>[1]["lang"],
      theme: "vesper",
    }),
  );

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
