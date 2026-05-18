// Framework-neutral preprocessor that swaps fenced code blocks for JSX
// component calls (`<ShikiBlock />` and `<MermaidBlock />`). The Shiki call
// is async and provided by the caller so the package never imports `shiki`
// directly — apps decide whether to run it server-side or client-side.

import type { WikiLinkKind } from "./wiki-links-types";

export interface PreprocessedSource {
  source: string;
}

export type ShikiHighlighter = (
  code: string,
  lang: string,
) => Promise<string>;

/**
 * Resolver function passed into the preprocessor by the host app.
 *
 * Given the raw anchor text inside backticks (e.g. ``src/foo.py``),
 * returns either a ``{ pageId, kind }`` pair when the ref maps to
 * a known wiki page, or ``null`` when it should render as plain
 * inline code. The host decides where ``pageId`` becomes a URL.
 */
export type WikiLinkResolver = (
  anchor: string,
) => { pageId: string; kind: WikiLinkKind } | null;

/**
 * Build a URL for a resolved wiki link. Default routes under
 * ``/repos/{repoId}/wiki/{pageId}``; callers can override per app.
 */
export type WikiLinkUrlBuilder = (
  pageId: string,
  kind: WikiLinkKind,
) => string;

export interface PreprocessOptions {
  highlight: ShikiHighlighter;
  resolveWikiLink?: WikiLinkResolver;
  buildWikiLinkUrl?: WikiLinkUrlBuilder;
}

// Inline backtick refs — single-backtick, identifier-ish content. Excludes
// triple-backtick fences (handled earlier) and ignores refs that already
// look like part of a URL or contain whitespace.
const INLINE_BACKTICK_RE = /(?<!`)`([^`\n]+?)`(?!`)/g;

function rewriteInlineBackticks(
  text: string,
  resolver: WikiLinkResolver,
  buildUrl: WikiLinkUrlBuilder,
): string {
  return text.replace(INLINE_BACKTICK_RE, (whole, raw: string) => {
    const ref = raw.trim();
    if (!ref) return whole;
    const resolved = resolver(ref);
    if (!resolved) return whole;
    const href = buildUrl(resolved.pageId, resolved.kind);
    // MDX consumes the JSX literal; the host package provides the
    // ``WikiLink`` component via its ``components`` map.
    return `<WikiLink href=${JSON.stringify(href)} kind=${JSON.stringify(
      resolved.kind,
    )}>${escapeMdxText(ref)}</WikiLink>`;
  });
}

function escapeMdxText(s: string): string {
  // MDX treats `{` and `<` specially. Escape both so a ref like
  // ``Map<string, int>`` doesn't open a JSX tag.
  return s.replace(/[<{]/g, (ch) => `{"${ch}"}`);
}

export async function preprocessWikiCodeBlocks(
  content: string,
  highlightOrOpts: ShikiHighlighter | PreprocessOptions,
): Promise<PreprocessedSource> {
  // Backwards-compatible signature: previous callers passed the
  // highlighter directly. New callers pass the options object so they
  // can include the wiki-link resolver.
  const opts: PreprocessOptions =
    typeof highlightOrOpts === "function"
      ? { highlight: highlightOrOpts }
      : highlightOrOpts;
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
      html = await opts.highlight(code, lang);
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

  // Inline backtick rewriting runs AFTER fence replacement so we never
  // touch the source inside fenced code samples (replaced earlier with
  // JSX tags that the regex skips).
  if (opts.resolveWikiLink) {
    const buildUrl =
      opts.buildWikiLinkUrl ?? ((pid) => `/wiki/${encodeURIComponent(pid)}`);
    source = rewriteInlineBackticks(source, opts.resolveWikiLink, buildUrl);
  }

  return { source };
}
