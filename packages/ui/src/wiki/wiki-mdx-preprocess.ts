// Framework-neutral preprocessor that swaps fenced code blocks for JSX
// component calls (`<ShikiBlock />` and `<MermaidBlock />`). The Shiki call
// is async and provided by the caller so the package never imports `shiki`
// directly — apps decide whether to run it server-side or client-side.

export interface PreprocessedSource {
  source: string;
}

export type ShikiHighlighter = (
  code: string,
  lang: string,
) => Promise<string>;

export async function preprocessWikiCodeBlocks(
  content: string,
  highlight: ShikiHighlighter,
): Promise<PreprocessedSource> {
  const fenceRe = /```(\w*)\n([\s\S]*?)```/g;
  const replacements: Array<{ placeholder: string; jsx: string }> = [];

  let match: RegExpExecArray | null;
  let idx = 0;
  while ((match = fenceRe.exec(content)) !== null) {
    const lang = match[1] || "text";
    const code = match[2] ?? "";

    if (lang === "mermaid") {
      const chart = JSON.stringify(code);
      replacements.push({
        placeholder: `\`\`\`${lang}\n${code}\`\`\``,
        jsx: `<MermaidBlock chart={${chart}} />`,
      });
      idx++;
      continue;
    }

    let html = "";
    try {
      html = await highlight(code, lang);
    } catch {
      html = `<pre><code>${code.replace(/</g, "&lt;")}</code></pre>`;
    }

    const htmlProp = JSON.stringify(html);
    const codeProp = JSON.stringify(code);
    replacements.push({
      placeholder: `\`\`\`${lang}\n${code}\`\`\``,
      jsx: `<ShikiBlock html={${htmlProp}} code={${codeProp}} lang="${lang}" />`,
    });
    idx++;
  }

  let source = content;
  for (const { placeholder, jsx } of replacements) {
    source = source.replace(placeholder, jsx);
  }
  return { source };
}
