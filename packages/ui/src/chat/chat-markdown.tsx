"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

const components: Components = {
  h1: ({ children }) => (
    <h1 className="text-base font-semibold text-[var(--color-text-primary)] mt-4 mb-2">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mt-3 mb-1.5">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-sm font-medium text-[var(--color-text-primary)] mt-2 mb-1">
      {children}
    </h3>
  ),
  p: ({ children }) => (
    <p className="text-sm text-[var(--color-text-secondary)] leading-relaxed mb-2">
      {children}
    </p>
  ),
  ul: ({ children }) => (
    <ul className="list-disc ml-4 text-sm text-[var(--color-text-secondary)] space-y-0.5 mb-2">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal ml-4 text-sm text-[var(--color-text-secondary)] space-y-0.5 mb-2">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  code: ({ className, children, ...props }) => {
    const isBlock = className?.includes("language-");
    if (isBlock) {
      return (
        <pre className="my-2 rounded-md bg-[var(--color-bg-inset)] border border-[var(--color-border-default)] p-3 overflow-x-auto">
          <code
            className="text-xs font-mono text-[var(--color-text-primary)]"
            {...props}
          >
            {children}
          </code>
        </pre>
      );
    }
    return (
      <code
        className="rounded px-1.5 py-0.5 bg-[var(--color-bg-elevated)] text-[var(--color-accent-primary)] text-xs font-mono"
        {...props}
      >
        {children}
      </code>
    );
  },
  pre: ({ children }) => <>{children}</>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-[var(--color-accent-primary)] pl-3 my-2 text-sm text-[var(--color-text-secondary)] italic">
      {children}
    </blockquote>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      className="text-[var(--color-accent-primary)] hover:underline"
      target="_blank"
      rel="noopener noreferrer"
    >
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-2">
      <table className="text-xs w-full border-collapse">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="text-left px-2 py-1 border-b border-[var(--color-border-default)] text-[var(--color-text-tertiary)] font-medium uppercase tracking-wider text-[10px]">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-2 py-1 border-b border-[var(--color-border-default)] text-[var(--color-text-secondary)]">
      {children}
    </td>
  ),
  strong: ({ children }) => (
    <strong className="font-medium text-[var(--color-text-primary)]">
      {children}
    </strong>
  ),
  hr: () => <hr className="my-3 border-[var(--color-border-default)]" />,
};

interface ChatMarkdownProps {
  content: string;
}

export function ChatMarkdown({ content }: ChatMarkdownProps) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {content}
    </ReactMarkdown>
  );
}
