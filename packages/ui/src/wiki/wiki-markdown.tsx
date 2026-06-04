"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { MermaidDiagram } from "./mermaid-diagram";
import { useState, useEffect, useMemo } from "react";
import { Copy, Check } from "lucide-react";
import { cn } from "../lib/cn";
import {
  buildAnchorIndex,
  type WikiLinkKind,
  type WikiLinkRef,
} from "./wiki-links-types";

/**
 * Resolves an inline backtick ref to an internal wiki page href, or null
 * when the ref doesn't map to a known page (renders as a plain code span).
 */
type WikiLinkLookup = (anchor: string) => { href: string; kind: WikiLinkKind } | null;

type WikiLinkComponent = React.ElementType<{
  href: string;
  className?: string;
  title?: string;
  children: React.ReactNode;
}>;

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-");
}

function ClientCodeBlock({ code, language }: { code: string; language: string }) {
  const [copied, setCopied] = useState(false);
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("shiki")
      .then(({ codeToHtml }) =>
        // Dual themes: tokens carry --shiki-light/--shiki-dark CSS vars and
        // globals.css picks one per theme (see ".shiki span" rules there).
        codeToHtml(code, {
          lang: language as never,
          themes: { light: "github-light", dark: "vesper" },
          defaultColor: false,
        }),
      )
      .then((result) => {
        if (!cancelled) setHtml(result);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [code, language]);

  async function copy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="group relative my-4 rounded-lg border border-[var(--color-border-default)] overflow-hidden">
      <div className="flex items-center justify-between px-4 py-1.5 bg-[var(--color-bg-elevated)] border-b border-[var(--color-border-default)]">
        {language && (
          <span className="text-xs font-mono text-[var(--color-text-tertiary)]">
            {language}
          </span>
        )}
        <button
          onClick={copy}
          className={cn(
            "ml-auto flex items-center gap-1 text-xs text-[var(--color-text-tertiary)]",
            "hover:text-[var(--color-text-secondary)] transition-colors",
          )}
          aria-label="Copy code"
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5" />
              Copied
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              Copy
            </>
          )}
        </button>
      </div>
      {html ? (
        <div
          className="overflow-x-auto text-sm [&>pre]:p-4 [&>pre]:m-0 [&>pre]:bg-transparent!"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="overflow-x-auto p-4 bg-[var(--color-bg-inset)]">
          <code className="text-xs font-mono text-[var(--color-text-primary)]">
            {code}
          </code>
        </pre>
      )}
    </div>
  );
}

function buildComponents(
  resolveLink: WikiLinkLookup | null,
  LinkComponent: WikiLinkComponent,
): Components {
  const Link = LinkComponent;
  return {
  h1: ({ children }) => {
    const text = typeof children === "string" ? children : extractText(children);
    const id = slugify(text);
    return (
      <h1 id={id} className="mt-10 mb-4 font-serif text-3xl font-semibold tracking-tight text-[var(--color-text-primary)] first:mt-0 scroll-mt-16">
        {children}
      </h1>
    );
  },
  h2: ({ children }) => {
    const text = typeof children === "string" ? children : extractText(children);
    const id = slugify(text);
    return (
      <h2 id={id} className="mt-9 mb-3 font-serif text-2xl font-semibold tracking-tight text-[var(--color-text-primary)] scroll-mt-16">
        {children}
      </h2>
    );
  },
  h3: ({ children }) => {
    const text = typeof children === "string" ? children : extractText(children);
    const id = slugify(text);
    return (
      <h3 id={id} className="mt-7 mb-2 font-serif text-xl font-semibold text-[var(--color-text-primary)] scroll-mt-16">
        {children}
      </h3>
    );
  },
  p: ({ children }) => (
    <p className="mb-4 text-base leading-[1.75] text-[var(--color-text-secondary)]">
      {children}
    </p>
  ),
  ul: ({ children }) => (
    <ul className="mb-4 ml-4 space-y-1.5 list-disc text-base text-[var(--color-text-secondary)]">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-4 ml-4 space-y-1.5 list-decimal text-base text-[var(--color-text-secondary)]">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="leading-7">{children}</li>,
  code: ({ className, children, ...props }) => {
    const langMatch = className?.match(/language-(\w+)/);
    const lang = langMatch?.[1];
    const isBlock = !!lang;

    if (isBlock) {
      const code = typeof children === "string" ? children : String(children ?? "");
      const trimmed = code.replace(/\n$/, "");

      if (lang === "mermaid") {
        return <MermaidDiagram chart={trimmed} />;
      }

      return <ClientCodeBlock code={trimmed} language={lang} />;
    }

    // Inline code: if the ref resolves to a known wiki page, render it as
    // a clickable internal link instead of a dead code span. We already
    // computed these targets in interlinking.py — just surface them.
    const text = typeof children === "string" ? children : extractText(children);
    const resolved = resolveLink ? resolveLink(text.trim()) : null;
    if (resolved) {
      return (
        <Link
          href={resolved.href}
          title={`Go to ${text.trim()}`}
          className="rounded bg-[var(--color-accent-muted)] px-1.5 py-0.5 text-[0.85em] font-mono text-[var(--color-accent-primary)] underline decoration-dotted underline-offset-2 hover:bg-[var(--color-accent-primary)] hover:text-white transition-colors"
        >
          {children}
        </Link>
      );
    }

    return (
      <code
        className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-[0.85em] font-mono text-[var(--color-accent-primary)]"
        {...props}
      >
        {children}
      </code>
    );
  },
  pre: ({ children }) => <>{children}</>,
  blockquote: ({ children }) => (
    <blockquote
      className="my-4 border-l-2 border-[var(--color-accent-primary)] pl-4 text-sm italic text-[var(--color-text-tertiary)]"
    >
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <div className="my-4 overflow-x-auto rounded border border-[var(--color-border-default)]">
      <table className="w-full text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-[var(--color-bg-elevated)]">{children}</thead>
  ),
  th: ({ children }) => (
    <th className="px-4 py-2 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-4 py-2 text-sm text-[var(--color-text-secondary)] border-t border-[var(--color-border-default)]">
      {children}
    </td>
  ),
  hr: () => <hr className="my-6 border-[var(--color-border-default)]" />,
  a: ({ href, children }) => (
    <a
      href={href}
      className="text-[var(--color-accent-primary)] underline underline-offset-2 hover:opacity-80 transition-opacity"
      target="_blank"
      rel="noopener noreferrer"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-[var(--color-text-primary)]">
      {children}
    </strong>
  ),
  };
}

function extractText(node: unknown): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (node && typeof node === "object" && "props" in node) {
    const props = (node as { props?: { children?: unknown } }).props;
    return extractText(props?.children);
  }
  return "";
}

interface WikiMarkdownProps {
  content: string;
  /**
   * Resolved forward links for this page (``page.metadata.wiki_links``).
   * When provided alongside ``buildHref``, inline backtick refs that match
   * a link anchor render as clickable internal links.
   */
  wikiLinks?: WikiLinkRef[];
  /** Maps a resolved ``{ pageId, kind }`` to an in-app href. */
  buildHref?: (pageId: string, kind: WikiLinkKind) => string;
  /** Router-aware link (e.g. Next.js ``Link``). Defaults to a plain ``<a>``. */
  LinkComponent?: WikiLinkComponent;
}

export function WikiMarkdown({
  content,
  wikiLinks,
  buildHref,
  LinkComponent = "a",
}: WikiMarkdownProps) {
  const components = useMemo(() => {
    let resolveLink: WikiLinkLookup | null = null;
    if (wikiLinks && wikiLinks.length > 0 && buildHref) {
      const index = buildAnchorIndex(wikiLinks);
      resolveLink = (anchor) => {
        const hit = index.get(anchor);
        if (!hit) return null;
        return { href: buildHref(hit.pageId, hit.kind), kind: hit.kind };
      };
    }
    return buildComponents(resolveLink, LinkComponent);
  }, [wikiLinks, buildHref, LinkComponent]);

  // Parsing + reconciling a large markdown document is the dominant cost when a
  // page opens. Memoize the rendered tree on (content, components) so unrelated
  // parent re-renders — sidebar toggles, hover state, etc. — don't re-parse it.
  return useMemo(
    () => (
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    ),
    [content, components],
  );
}
