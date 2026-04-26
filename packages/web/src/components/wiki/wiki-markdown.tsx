"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { MermaidDiagram } from "./mermaid-diagram";
import { useState } from "react";
import { Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils/cn";

/**
 * Client-side wiki markdown renderer with Mermaid diagram support and
 * heading anchor IDs (for ToC scroll-to-heading).
 *
 * This replaces ChatMarkdown in the docs explorer so that Mermaid diagrams
 * render inline and ToC links work correctly.
 */

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-");
}

function ClientCodeBlock({ code, language }: { code: string; language: string }) {
  const [copied, setCopied] = useState(false);

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
      <pre className="overflow-x-auto p-4 bg-[var(--color-bg-inset)]">
        <code className="text-xs font-mono text-[var(--color-text-primary)]">
          {code}
        </code>
      </pre>
    </div>
  );
}

const components: Components = {
  h1: ({ children }) => {
    const text = typeof children === "string" ? children : extractText(children);
    const id = slugify(text);
    return (
      <h1 id={id} className="mt-8 mb-4 text-xl font-semibold text-[var(--color-text-primary)] first:mt-0 scroll-mt-16">
        {children}
      </h1>
    );
  },
  h2: ({ children }) => {
    const text = typeof children === "string" ? children : extractText(children);
    const id = slugify(text);
    return (
      <h2 id={id} className="mt-6 mb-3 text-lg font-semibold text-[var(--color-text-primary)] scroll-mt-16">
        {children}
      </h2>
    );
  },
  h3: ({ children }) => {
    const text = typeof children === "string" ? children : extractText(children);
    const id = slugify(text);
    return (
      <h3 id={id} className="mt-5 mb-2 text-base font-semibold text-[var(--color-text-primary)] scroll-mt-16">
        {children}
      </h3>
    );
  },
  p: ({ children }) => (
    <p className="mb-4 text-sm leading-7 text-[var(--color-text-secondary)]">
      {children}
    </p>
  ),
  ul: ({ children }) => (
    <ul className="mb-4 ml-4 space-y-1 list-disc text-sm text-[var(--color-text-secondary)]">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-4 ml-4 space-y-1 list-decimal text-sm text-[var(--color-text-secondary)]">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="leading-6">{children}</li>,
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

    return (
      <code
        className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-xs font-mono text-[var(--color-accent-primary)]"
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
}

export function WikiMarkdown({ content }: WikiMarkdownProps) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {content}
    </ReactMarkdown>
  );
}
