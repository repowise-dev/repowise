"use client";

/**
 * The one themed markdown renderer.
 *
 * Used by chat replies, decision records, the graph doc panel and the artifact
 * panel. It exists so none of those reach for `prose` — Tailwind Typography is
 * banned on our markdown, because every element here is already themed through
 * our tokens and the plugin only contributes damage (it prints literal
 * backticks via `code::before`, and `prose-invert` is a static class that
 * cannot follow the theme).
 *
 * Chat is a *reading* surface, not a chrome surface: an answer is read top to
 * bottom the way a wiki page is, so it runs the reading scale from the design
 * language (16px body, a real heading ladder) rather than the 12/14 chrome
 * sizes it used to. Headings previously landed at 16/14/14 against 14px body,
 * which is the "bolded body" failure — a heading that differs from the text
 * around it by weight alone.
 *
 * The face stays sans. Serif is bounded to the named wiki reading surfaces so
 * the mark keeps meaning "this is a document"; a reply is prose, not a document.
 *
 * `density="compact"` keeps the old chrome scale for consumers that render the
 * same markdown inside a narrow panel, where 16px body would not fit the column
 * it is given. One component, one prop — not a second renderer.
 *
 * Inline code is deliberately *not* accent-coloured. Nothing here resolves to a
 * page, so an accent path would be decoration on something that does not
 * respond — the exact anti-pattern rule 9 was written for, and the one the wiki
 * renderer already fixed.
 */

import { lazy, Suspense, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { CodeFrame, HighlightedCodeBlock } from "./code-block";

// Mermaid pulls a heavy renderer that is strictly client-side. Lazy-load it so
// it never enters the chat bundle (or SSR pass) unless a reply actually contains
// an inline ```mermaid block — parity with the wiki/docs reader.
const MermaidDiagram = lazy(() =>
  import("../wiki/mermaid-diagram").then((m) => ({ default: m.MermaidDiagram })),
);

const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

export type MarkdownDensity = "reading" | "compact";

interface Scale {
  h1: string;
  h2: string;
  h3: string;
  body: string;
  list: string;
  block: string;
  code: string;
  gap: string;
  listIndent: string;
  rule: string;
}

const SCALES: Record<MarkdownDensity, Scale> = {
  reading: {
    h1: "text-2xl mt-8 mb-3",
    h2: "text-xl mt-7 mb-2.5",
    h3: "text-lg mt-6 mb-2",
    body: "text-base",
    list: "text-[15.5px] space-y-1.5",
    block: "my-4",
    code: "text-sm",
    gap: "mb-4",
    listIndent: "ml-5",
    rule: "my-6",
  },
  compact: {
    h1: "text-base mt-4 mb-2",
    h2: "text-sm mt-3 mb-1.5",
    h3: "text-sm mt-2 mb-1",
    body: "text-xs",
    list: "text-xs space-y-1",
    block: "my-2",
    code: "text-[11px]",
    gap: "mb-2",
    listIndent: "ml-4",
    rule: "my-3",
  },
};

function buildComponents(s: Scale, streaming: boolean): Components {
  const measure = s.body === "text-base" ? "max-w-[72ch]" : "max-w-full";
  return {
    h1: ({ children }) => (
      <h1
        className={`${s.h1} ${measure} font-semibold text-[var(--color-text-primary)] [overflow-wrap:anywhere]`}
      >
        {children}
      </h1>
    ),
    h2: ({ children }) => (
      <h2
        className={`${s.h2} ${measure} font-semibold text-[var(--color-text-primary)] [overflow-wrap:anywhere]`}
      >
        {children}
      </h2>
    ),
    h3: ({ children }) => (
      <h3
        className={`${s.h3} ${measure} font-semibold text-[var(--color-text-primary)] [overflow-wrap:anywhere]`}
      >
        {children}
      </h3>
    ),
    p: ({ children }) => (
      <p
        className={`${s.body} ${s.gap} ${measure} text-[var(--color-text-primary)] leading-relaxed [overflow-wrap:anywhere]`}
      >
        {children}
      </p>
    ),
    ul: ({ children }) => (
      <ul
        className={`list-disc ${s.listIndent} ${s.list} ${s.gap} ${measure} text-[var(--color-text-primary)] [&.contains-task-list]:ml-0 [&.contains-task-list]:list-none`}
      >
        {children}
      </ul>
    ),
    ol: ({ children }) => (
      <ol
        className={`list-decimal ${s.listIndent} ${s.list} ${s.gap} ${measure} text-[var(--color-text-primary)]`}
      >
        {children}
      </ol>
    ),
    li: ({ children, className }) => <li className={`${className ?? ""} leading-relaxed [overflow-wrap:anywhere] [&>p]:mb-1`}>{children}</li>,
    input: ({ type, node: _node, ...props }) => type === "checkbox" ? (
      <input type="checkbox" disabled className="mr-2 align-middle accent-[var(--color-accent-secondary)]" {...props} />
    ) : <input type={type} {...props} />,
    code: ({ className, children, node: _node, ...props }) => {
      const declaredLanguage = className?.match(/language-([^\s]+)/)?.[1];
      const rawCode = String(children ?? "");
      const isBlock = Boolean(declaredLanguage) || rawCode.includes("\n");
      if (isBlock) {
        const language = declaredLanguage ?? "text";
        const code = rawCode.replace(/\n$/, "");
        if (language === "mermaid") {
          return (
            <CodeFrame code={code} language={language} compact={s.body !== "text-base"}>
              {streaming ? (
                <pre className="m-0 min-w-max p-4"><code className="font-mono text-[var(--color-text-primary)]">{code}</code></pre>
              ) : (
                <Suspense fallback={<pre className="m-0 min-w-max p-4"><code className="font-mono text-[var(--color-text-primary)]">{code}</code></pre>}>
                  <MermaidDiagram chart={code} />
                </Suspense>
              )}
            </CodeFrame>
          );
        }
        return <HighlightedCodeBlock code={code} language={language} compact={s.body !== "text-base"} streaming={streaming} />;
      }
      // 0.85em, relative: Geist Mono runs wider and taller than Geist at the
      // same nominal size, so 1em mono inside 16px sans reads oversized.
      return (
        <code
          className="break-all whitespace-normal rounded px-1 py-0.5 bg-[var(--color-bg-inset)] text-[var(--color-text-primary)] text-[0.85em] font-mono [overflow-wrap:anywhere]"
          {...props}
        >
          {children}
        </code>
      );
    },
    pre: ({ children }) => <>{children}</>,
    blockquote: ({ children }) => (
      <blockquote
        className={`${s.block} ${s.body} ${measure} border-l-2 border-[var(--color-border-default)] pl-4 text-[var(--color-text-secondary)] [overflow-wrap:anywhere]`}
      >
        {children}
      </blockquote>
    ),
    a: ({ href, children }) => (
      <a
        href={href}
        className="text-[var(--color-accent-primary)] underline underline-offset-2 [overflow-wrap:anywhere]"
        target="_blank"
        rel="noopener noreferrer"
      >
        {children}
      </a>
    ),
    table: ({ children }) => (
      <div className={`${s.block} max-w-full overflow-x-auto overscroll-x-contain border-y border-[var(--color-border-default)]`}>
        <table className={`${s.code} w-full min-w-[640px] border-collapse`}>{children}</table>
      </div>
    ),
    thead: ({ children }) => (
      <thead className="border-b border-[var(--color-border-default)]">
        {children}
      </thead>
    ),
    th: ({ children }) => (
      <th scope="col" className={`text-left px-3 py-2.5 font-normal ${MICRO_LABEL}`}>
        {children}
      </th>
    ),
    tr: ({ children }) => (
      <tr className="border-t border-[var(--color-border-default)]">
        {children}
      </tr>
    ),
    td: ({ children }) => (
      <td className="px-3 py-2.5 align-top text-[var(--color-text-primary)] tabular-nums [overflow-wrap:anywhere]">
        {children}
      </td>
    ),
    strong: ({ children }) => (
      <strong className="font-semibold text-[var(--color-text-primary)]">
        {children}
      </strong>
    ),
    hr: () => (
      <hr className={`${s.rule} border-t border-[var(--color-border-default)]`} />
    ),
  };
}

export interface MarkdownProps {
  content: string;
  /** `compact` keeps chrome sizes for narrow panels. */
  density?: MarkdownDensity;
  /** Defers expensive code/diagram rendering until the answer is complete. */
  streaming?: boolean;
}

export function Markdown({ content, density = "reading", streaming = false }: MarkdownProps) {
  const components = useMemo(
    () => buildComponents(SCALES[density], streaming),
    [density, streaming],
  );
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {content}
    </ReactMarkdown>
  );
}

/**
 * @deprecated Use `Markdown`. Kept because `@repowise-dev/ui/chat/chat-markdown`
 * is imported by the hosted frontend in several places.
 */
export const ChatMarkdown = Markdown;
export type ChatMarkdownDensity = MarkdownDensity;
